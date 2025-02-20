from __future__ import annotations

import io

from functools import wraps
from typing import Optional


class Token:
    def __init__(self, value: str, lineno: int, start: int, end: int):
        self.value = value
        self.lineno = lineno
        self.start = start
        self.end = end

    def __repr__(self):
        lineno, start, end = self.lineno, self.start, self.end
        return f"Token({self.value!r}, {lineno=}, {start=}, {end=})"

    def __str__(self):
        return repr(self.value)


class Reader:
    """Reads the file and produces a stream of characters."""

    def __init__(self, stream: str | io.TextIOBase, bufsize=4096):
        self.buffer = ""
        self.stream = None
        self.name = None
        self.bufsize = bufsize
        self.eof = False
        self.pointer = 0
        self.line = 1
        self.column = 0

        if isinstance(stream, str):
            self.name = "<unicode string>"
            self.buffer = stream
        elif isinstance(stream, io.IOBase):
            self.name = getattr(stream, 'name', '<file>')
            self.stream = stream
            self.eof = False

            if not stream.readable():
                with_name = f": {self.name}" if self.name else ""
                raise ValueError("stream must be readable" + with_name)

    def __iter__(self) -> Reader:
        return self

    def __next__(self) -> Token:
        try:
            char = self.buffer[self.pointer]
        except IndexError:
            if self.stream:
                self.update()
            try:
                char = self.buffer[self.pointer]
            except IndexError:
                self.eof = True
                raise StopIteration
        if char in '\r\n':
            self.line += 1
            self.column = 0
        else:
            self.column += 1
        self.pointer += 1
        return Token(char, self.line, self.pointer - 1, self.pointer)

    def update(self, length: int = 1) -> None:
        assert self.stream
        if self.eof:
            return
        self.buffer = self.buffer[self.pointer:]
        self.pointer = 0
        while len(self.buffer) < length:
            data = self.stream.read(self.bufsize)
            if data:
                self.buffer += data
            else:
                self.eof = True
                break


class MemoEntry:
    """A record in the memo table.

    The primary purpose of this class is to provide a wrapper that is stored
    by reference. This allows to change record's data without accessing
    it in the memo table.
    """

    def __init__(self, result: Optional[list[Token] | Token], pos: int):
        self.result = result
        self.pos = pos

    def __repr__(self):
        result, pos = self.result, self.pos
        return f"MemoEntry({result!r}, {pos=})"

    def __str__(self):
        return repr(self)


def memoize(fn):

    @wraps(fn)
    def wrapper(self, *args):
        pos = self._mark()
        key = (fn, args, pos)
        memo = self._memos.get(key)
        if memo is None:
            result = fn(self, *args)
            endpos = self._mark()
            self._memos[key] = MemoEntry(result, endpos)
        else:
            result = memo.result
            self._reset(memo.pos)

        return result

    return wrapper


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


class Parser:
    def __init__(self, reader: Reader):
        self._memos = {}
        self._reader = reader
        self._chars = []
        self._pos = 0

        # "Detecting the left recursive rules and creating the grow rules
        # can be performed before the parser is used"
        self._grow_rules = {
            "Expr": [self.Expr_Alt_3, self.Expr_Alt_1, self.Expr_Alt_2],
            "Mul": [self.Mul_Alt_3, self.Mul_Alt_1, self.Mul_Alt_2],
            "Primary": [self.Primary_Alt_3, self.Primary_Alt_1,
                        self.Primary_Alt_2],
            "L": [self.L_Alt_2, self.L_Alt_1],
            "P": [self.P_Alt_2, self.P_Alt_1]
        }

    @memoize
    def _expectc(self, char: Optional[str] = None) -> Optional[Token]:
        """Expects a single character in the stream"""
        if c := self._peek_char():
            if char is None or c.value == char:
                self._pos += 1
                return c
        return None

    @memoize
    def _expects(self, string: str) -> Optional[Token]:
        """Expects a sequence of characters in the stream"""
        pos = self._mark()
        for c in string:
            nextch = self._peek_char()
            if nextch is None or c != nextch.value:
                self._reset(pos)
                return None
            self._pos += 1

        lineno, start, end = self._reader.line, pos, self._pos
        return Token(string, lineno, start, end)

    def _lookahead(self, positive, fn, *args) -> Optional[list]:
        """Expects an expression defined by `fn`

        Succeeds, if `fn` succeeds and `positive` is True, or if `fn` fails
        and `positive` is False. Does not consume input.
        """
        pos = self._mark()
        ok = fn(*args) is not None
        self._reset(pos)
        if ok == positive:
            return []
        return None

    def _loop(self, nonempty, fn, *args) -> Optional[list[Token]]:
        """Repeatedly tries an expression defined by `fn`

        Halts if `fn` fails or no further progress is made. If `nonempty` is
        True, works as One or More quantifier (`+`), requiring `fn` succeed
        at least once. Otherwise, succeeds even if `fn` did not succeed at all.
        """
        pos = lastpos = self._mark()
        tokens = []
        while (token := fn(*args)) is not None and self._mark() > lastpos:
            tokens.append(token)
            lastpos = self._pos
        if len(tokens) >= nonempty:
            return tokens
        self._reset(pos)
        return None

    def _ranges(self, *ranges: tuple[str, str]) -> Optional[Token]:
        """Expects the character to be in given ranges

        Ranges are defined by tuples of single-character strings, where
        the first element is less than or equal to the second:
        `tup[0] <= tup[1]`. Succeeds, if `tup[0] <= character <= tu[1]` for
        any tuple.
        """
        char = self._peek_char()
        if char is None:
            return None
        value = char.value
        for beg, end in ranges:
            if value >= beg and value <= end:
                self._pos += 1
                return char

    def _get_char(self) -> Optional[Token]:
        """Return the character and advance pointer to the next character"""
        char = self._peek_char()
        self._pos += 1
        return char

    def _peek_char(self) -> Optional[Token]:
        """Return the character and without moving the pointer"""
        if self._pos == len(self._chars):
            self._chars.append(next(self._reader, None))
        return self._chars[self._pos]

    def _mark(self) -> int:
        """Get the pointer"""
        return self._pos

    def _reset(self, pos: int):
        """Reset the pointer to position"""
        self._pos = pos

    def parse(self):
        return self.Grammar()

    @memoize
    def Grammar(self):
        pos = self._mark()
        if (
            (expr := self.Expr()) is not None and
            self.EOF() is not None
        ):
            return expr
        self._reset(pos)
        return None

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
            (expr := self.Expr()) is not None and
            (plus := self.PLUS()) is not None and
            (mul := self.Mul()) is not None
        ):
            return [expr, plus, mul]
        return None

    def Expr_Alt_2(self):
        if (
            (expr := self.Expr()) is not None and
            (minus := self.MINUS()) is not None and
            (mul := self.Mul()) is not None
        ):
            return [expr, minus, mul]
        return None

    def Expr_Alt_3(self):
        if (
            (mul := self.Mul()) is not None
        ):
            return mul
        return None

    @memoize_lr
    def Mul(self):
        pos = self._mark()
        if (alt := self.Mul_Alt_1()) is not None:
            return alt
        self._reset(pos)
        if (alt := self.Mul_Alt_2()) is not None:
            return alt
        self._reset(pos)
        if (alt := self.Mul_Alt_3()) is not None:
            return alt
        self._reset(pos)
        return None

    def Mul_Alt_1(self):
        if (
            (mul := self.Mul()) is not None and
            (mul_1 := self.MUL()) is not None and
            (term := self.Term()) is not None
        ):
            return [mul, mul_1, term]
        return None

    def Mul_Alt_2(self):
        if (
            (mul := self.Mul()) is not None and
            (div := self.DIV()) is not None and
            (term := self.Term()) is not None
        ):
            return [mul, div, term]
        return None

    def Mul_Alt_3(self):
        if (
            (term := self.Term()) is not None
        ):
            return term
        return None

    @memoize
    def Term(self):
        pos = self._mark()
        if (
            (int := self.Int()) is not None
        ):
            return int
        self._reset(pos)
        if (
            (primary := self.Primary()) is not None
        ):
            return primary
        self._reset(pos)
        if (
            (mutual := self.Mutual()) is not None
        ):
            return mutual
        self._reset(pos)
        return None

    @memoize
    def Int(self):
        pos = self._mark()
        if (
            (_1 := self._ranges(('0', '9'))) is not None and
            self.WS() is not None
        ):
            return _1
        self._reset(pos)
        return None

    @memoize_lr
    def Primary(self):
        pos = self._mark()
        if (alt := self.Primary_Alt_1()) is not None:
            return alt
        self._reset(pos)
        if (alt := self.Primary_Alt_2()) is not None:
            return alt
        self._reset(pos)
        if (alt := self.Primary_Alt_3()) is not None:
            return alt
        self._reset(pos)
        return None

    def Primary_Alt_1(self):
        if (
            (methodinvocation := self.MethodInvocation()) is not None
        ):
            return methodinvocation
        return None

    def Primary_Alt_2(self):
        if (
            (fieldaccess := self.FieldAccess()) is not None
        ):
            return fieldaccess
        return None

    def Primary_Alt_3(self):
        if (
            (id := self.Id()) is not None
        ):
            return id
        return None

    def MethodInvocation(self):
        pos = self._mark()
        if (
            (primary := self.Primary()) is not None and
            self._expectc('.') is not None and
            (id := self.Id()) is not None and
            self.CALL() is not None
        ):
            return ["method_invocation", primary, id]
        self._reset(pos)
        if (
            (id := self.Id()) is not None and
            self.CALL() is not None
        ):
            return ["function_invocation", id]
        self._reset(pos)
        return None

    def FieldAccess(self):
        pos = self._mark()
        if (
            (primary := self.Primary()) is not None and
            self._expectc('.') is not None and
            (id := self.Id()) is not None
        ):
            return ["field_access", primary, id]
        self._reset(pos)
        return None

    @memoize
    def Mutual(self):
        pos = self._mark()
        if (
            (ll := self.L()) is not None
        ):
            return ll
        self._reset(pos)
        return None

    @memoize_lr
    def L(self):
        pos = self._mark()
        if (alt := self.L_Alt_1()) is not None:
            return alt
        self._reset(pos)
        if (alt := self.L_Alt_2()) is not None:
            return alt
        self._reset(pos)
        return None

    def L_Alt_1(self):
        if (
            (p := self.P()) is not None and
            self._expectc('.') is not None and
            (id := self.Id()) is not None
        ):
            return [p, id]
        return None

    def L_Alt_2(self):
        if (
            (_1 := self._expectc('$')) is not None
        ):
            return _1
        return None

    @memoize_lr
    def P(self):
        pos = self._mark()
        if (alt := self.P_Alt_1()) is not None:
            return alt
        self._reset(pos)
        if (alt := self.P_Alt_2()) is not None:
            return alt
        self._reset(pos)
        return None

    def P_Alt_1(self):
        if (
            (p := self.P()) is not None and
            (int := self.Int()) is not None
        ):
            return [p, int]
        return None

    def P_Alt_2(self):
        if (
            (ll := self.L()) is not None
        ):
            return ll
        return None

    @memoize
    def Id(self):
        pos = self._mark()
        if (
            (_1 := self._ranges(('a', 'z'))) is not None and
            self.WS() is not None
        ):
            return _1
        self._reset(pos)
        return None

    @memoize
    def PLUS(self):
        pos = self._mark()
        if (
            (c := self._expectc('+')) is not None and
            self.WS() is not None
        ):
            return c
        self._reset(pos)
        return None

    @memoize
    def MINUS(self):
        pos = self._mark()
        if (
            (c := self._expectc('-')) is not None and
            self.WS() is not None
        ):
            return c
        self._reset(pos)
        return None

    @memoize
    def MUL(self):
        pos = self._mark()
        if (
            (c := self._expectc('*')) is not None and
            self.WS() is not None
        ):
            return c
        self._reset(pos)
        return None

    @memoize
    def DIV(self):
        pos = self._mark()
        if (
            (c := self._expectc('/')) is not None and
            self.WS() is not None
        ):
            return c
        self._reset(pos)
        return None

    @memoize
    def LBRACE(self):
        pos = self._mark()
        if (
            (c := self._expectc('(')) is not None and
            self.WS() is not None
        ):
            return c
        self._reset(pos)
        return None

    @memoize
    def RBRACE(self):
        pos = self._mark()
        if (
            (c := self._expectc(')')) is not None and
            self.WS() is not None
        ):
            return c
        self._reset(pos)
        return None

    @memoize
    def CALL(self):
        pos = self._mark()
        if (
            (c := self._expects("()")) is not None and
            self.WS() is not None
        ):
            return c
        self._reset(pos)
        return None

    @memoize
    def WS(self):
        pos = self._mark()
        if (
            (_1 := self._loop(False, self.Spacing)) is not None
        ):
            return _1
        self._reset(pos)
        return None

    @memoize
    def Spacing(self):
        pos = self._mark()
        if (
            (_1 := self._expectc(' ')) is not None
        ):
            return _1
        self._reset(pos)
        if (
            (_1 := self._expectc('\r')) is not None
        ):
            return _1
        self._reset(pos)
        if (
            (_1 := self._expectc('\n')) is not None
        ):
            return _1
        self._reset(pos)
        if (
            (_1 := self._expectc('\t')) is not None
        ):
            return _1
        self._reset(pos)
        return None

    @memoize
    def EOF(self):
        pos = self._mark()
        if (
            self._lookahead(False, self._expectc) is not None
        ):
            return []
        self._reset(pos)
        return None


if __name__ == "__main__":
    from argparse import ArgumentParser, FileType
    import sys

    argparser = ArgumentParser()
    argparser.add_argument("input_file", nargs='?',
                           type=FileType('r', encoding='UTF-8'),
                           default=sys.stdin)

    ns = argparser.parse_args()

    reader = Reader(ns.input_file)
    parser = Parser(reader)

    result = parser.parse()
    if result is not None:
        print(repr(result))

    exit(result is None)  # 0 is success
