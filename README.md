# Toy Packrat parser with improved left recursion support

A demonstraional purpose implementation of a Packrat parser with a left
recursion algorithm in Python. I think, mainly for future me to not forget
how it works.

## The original algorithm

The algorithm implements a "grow LR" operator `*>`:

```
x <- S *> R1 / x R2 / ... / x R
```

The binary operator `*>` determines a seed from the left side expressions `S`,
then repeatedly evaluates the right hand expression with the ordered choices
of items `x R`.

<details>
<summary>Algorithm pseudocode</summary>

```
struct LRFail(heads: Set[Head], frame: Context)
struct MemoEntry(ans: Ast, pos: Position)
struct Head subclasses MemoEntry(chains: Array[Chain])

Memo(Rule, Pos) -> MemoEntry
Pos: int

ApplyRule(R, P)
    let m = Memo(R, P)
    if m = NIL then
        let e = new MemoEntry(new LRFail(NIL, thisContext), P)
        Memo(R, P) <- e
        e.ans <- Eval(R.body)
        e.pos <- Pos
        if e is Head then
            # Grow LR
            if e.ans is LRFail then
                e.ans <- Fail
            return ApplyGrowRule(e, P)
        return e.ans

    Pos <- m.pos
    if m.ans is LRFail then
        # LR detected
        if m.ans.heads = NIL then
            # New head
            m becomes Head(m.ans, m.pos, {})
            m.ans.heads <- {m}
        AddChain(thisContext, m.ans.heads)
    return m.ans

ApplyGrowRule(M, P)
    while True do
        # Left repetition while improvement
        Pos <- P
        for each c in M.chains do
            # Ordered choice
            let ans = Resume(c)  # On chain
            if ans != Fail then
                break
            Pos <- P
        if Pos <= M.pos then
            # No improvement
            Pos <- M.pos
            return M.ans
        M.ans <- ans
        M.pos <- Pos
```

</details>

## Algorithm implementation

The main idea of an algorithm is collecting left-recursive chains
(rules and alternatives involved in left-recursion) and repeated evaluation
of chains, until the evaluation does not result in further progression (the
position in token's stream remains the same).

To start the evaluation process, algorithm first needs to parse non-recursive
alternative, creating the basis for recursive alternatives' evaluation, which
is called the "seed parse"[^packrat-lr]. This requires the left-recursive
rule have a non-recursive alternative.

To be able to call alternatives of the rules, they must be implemented as
functions, not just as `if` blocks inside the rule function like in [^pegen].

Consider the following grammar:

```
Expr <- Expr PLUS Mul / Expr MINUS Mul / Mul
Mul  <- Mul MUL Term / Mul DIV Term / LBRACE Expr RBRACE / Term
Term <- Int / Primary / Mutual
```

`Expr` and `Mul` rules are directly left-recursive. In the source code `Expr`
rule is implemented as follows (some details are omitted for brevity).

<details>
<summary>`Expr` rule code</summary>

```python
@memoize_lr
def Expr(self):
    pos = self._mark()
    if (alt := self.Expr_Alt_1()) is not None:
        return alt
    self._reset(pos)
    if (alt := self.Expr_Alt_2()) is not None:
        return alt
    self._reset(pos)
    if (alt := self.Expr_Alt_3()) is not None:
        return alt
    self._reset(pos)
    return None

def Expr_Alt_1(self):
    if (
        (expr := self.Expr()) is not None and ...
    ):
        return [expr, plus, mul]
    return None

def Expr_Alt_2(self):
    if (
        (expr := self.Expr()) is not None and ...
    ):
        return [expr, minus, mul]
    return None

def Expr_Alt_3(self):
    if (
        (mul := self.Mul()) is not None
    ):
        return mul
    return None
```

</details>

Note that all alternatives are functions, even non-recursive one.

The chain for this rule is the following:

```python
self._grow_rules = {
    "Expr": [self.Expr_Alt_3, self.Expr_Alt_1, self.Expr_Alt_2]
}
```

The non-recursive alternative comes first to denote the order of evaluation,
though it can be placed in order of alternative appearance in the rule (with
the appropriate `memoize_lr` decorator code changes).

When the `memoize_lr` encounters that the `Expr` rule is called, it first
calls `Expr_Alt_3` - "plants the seed", then loops `Alt_1` and `Alt_2` in
that order to preserve ordered choise. If after iteration no progress
encountered (current position = position saved before the growing process),
function stops the loop and returns last saved result.

<details>
<summary>`memoize_lr` source code</summary>

```python
def memoize_lr(fn):

    this_context = fn.__name__

    @wraps(fn)
    def wrapper(self, *args):
        pos = self._mark()
        key = (fn, args, pos)
        memo = self._memos.get(key)

        if memo is None:
            alts = self._grow_rules[this_context]

            self._memos[key] = memo = MemoEntry(None, pos)

            # First plant the seed
            seed = alts[0]
            result = seed()
            if result is None:
                return None
            memo.result, memo.pos = result, self._pos

            # Then grow the LR, repeatedly calling recursive alternatives
            # until there is no improvement
            while True:
                self._pos = pos
                for alt in alts[1:]:
                    # Ordered choice
                    result = alt()
                    if result is not None:
                        break
                    self._pos = pos
                if self._pos <= memo.pos:
                    # No improvement
                    self._pos = memo.pos
                    return memo.result
                memo.result = result
                memo.pos = self._pos

        else:
            self._pos = memo.pos
            return memo.result

    return wrapper
```

</details>

As you may notice, I omitted the chain construction part. As stated
in[^improved-lr]: "Detecting the left recursive rules and creating the grow
rules can be performed before the parser is used".

## Other implementation notes

Rule implementation idea is mainly taken from [^pegen]. Rules are
implemented as Parser class methods, alternatives are `if` blocks, where
items in alternatives are enumerated with `and` operator, relying on its
lazy evaluation: if the left-hand side of an `and` operator results in false,
then it returns without trying its right-hand side.

File `parser.py` also contains the reader that implements iterator protocol
and produces a stream of Token instances.

## References

[^improved-lr]: Improved Packrat Parser Left Recursion Support. James R.
Douglass
[^packrat-lr]: Packrat Parsers Can Support Left Recursion. Alessandro Warth,
James R. Douglass, Todd Millstein
[^pegen]: [Pegen](https://github.com/we-like-parsers/pegen)
