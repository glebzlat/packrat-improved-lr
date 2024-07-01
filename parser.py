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
        return f"Token({self.value}, {lineno=}, {start=}, {end=})"

    def __str__(self):
        return repr(self)


class Reader:
    """
    Reads the file and produces a stream of characters.

    Reader supports strings and UTF-8 encoded streams only.
    """

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


def memoize(fn):

    @wraps(fn)
    def wrapper(self, *args):
        pos = self._mark()
        key = (fn, args, pos)
        memo = self._memos.get(key)
        if memo is None:
            result = fn(self, *args)
            endpos = self._mark()
            self._memos[key] = result, endpos
        else:
            result, endpos = memo
            self._reset(endpos)

        return result

    return wrapper


class Parser:
    def __init__(self, reader: Reader):
        self._memos = {}
        self._reader = reader
        self._chars = []
        self._pos = 0

    @memoize
    def _expectc(self, char: Optional[str] = None) -> Optional[Token]:
        if c := self._peek_char():
            if char is None or c.value == char:
                self._pos += 1
                return c
        return None

    @memoize
    def _expects(self, string: str) -> Optional[Token]:
        pos = self._mark()
        for c in string:
            nextch = self._peek_char()
            if c != nextch.value:
                self._reset(pos)
                return None
            self._pos += 1

        lineno, start, end = self._reader.line, pos, self._pos
        return Token(string, lineno, start, end)

    def _lookahead(self, positive, fn, *args) -> Optional[list]:
        pos = self._mark()
        ok = fn(*args) is not None
        self._reset(pos)
        if ok == positive:
            return []
        return None

    def _loop(self, nonempty, fn, *args) -> Optional[list[Token]]:
        pos = lastpos = self._mark()
        tokens = []
        while (token := fn(*args)) is not None and self._mark() > lastpos:
            tokens.append(token)
            lastpos = self._pos
        if len(tokens) >= nonempty:
            return tokens
        self._reset(pos)
        return None

    def _ranges(self, *ranges) -> Optional[Token]:
        char = self._peek_char()
        if char is None:
            return None
        value = char.value
        for beg, end in ranges:
            if value >= beg and value <= end:
                return char

    def _get_char(self) -> Optional[Token]:
        char = self._peek_char()
        self._pos += 1
        return char

    def _peek_char(self) -> Optional[Token]:
        if self._pos == len(self._chars):
            self._chars.append(next(self._reader, None))
        return self._chars[self._pos]

    def _mark(self) -> int:
        return self._pos

    def _reset(self, pos: int):
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

    @memoize
    def Expr(self):
        pos = self._mark()
        if (
            (expr := self.Expr()) is not None and
            (plus := self.PLUS()) is not None and
            (mul := self.Mul()) is not None
        ):
            return [expr, plus, mul]
        self._reset(pos)
        if (
            (expr := self.Expr()) is not None and
            (minus := self.MINUS()) is not None and
            (mul := self.Mul()) is not None
        ):
            return [expr, minus, mul]
        self._reset(pos)
        if (
            (mul := self.Mul()) is not None
        ):
            return mul
        self._reset(pos)
        return None

    @memoize
    def Mul(self):
        pos = self._mark()
        if (
            (mul := self.Mul()) is not None and
            (mul_1 := self.MUL()) is not None and
            (term := self.Term()) is not None
        ):
            return [mul, mul_1, term]
        self._reset(pos)
        if (
            (mul := self.Mul()) is not None and
            (div := self.DIV()) is not None and
            (term := self.Term()) is not None
        ):
            return [mul, div, term]
        self._reset(pos)
        if (
            (term := self.Term()) is not None
        ):
            return term
        self._reset(pos)
        return None

    @memoize
    def Term(self):
        pos = self._mark()
        if (
            (_1 := self._ranges(('0', '9'))) is not None and
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
            (c := self._expectc('+')) is not None and
            self.WS() is not None
        ):
            return c
        self._reset(pos)
        return None

    @memoize
    def MUL(self):
        pos = self._mark()
        if (
            (c := self._expectc('+')) is not None and
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
        print(result)

    exit(result is None)  # 0 is success
