import ast
from dataclasses import dataclass

COMPARE = {
    ast.Lt: "<=",
    ast.LtE: "<",
    ast.Gt: ">=",
    ast.GtE: ">",
    ast.Eq: "!=",
    ast.NotEq: "==",
    ast.Is: "is not",
    ast.IsNot: "is",
    ast.In: "not in",
    ast.NotIn: "in",
}
COMPARE_TEXT = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
}
ARITHMETIC = {ast.Add: "-", ast.Sub: "+", ast.Mult: "/", ast.Div: "*"}
ARITHMETIC_TEXT = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


@dataclass(frozen=True)
class Mutant:
    file: str
    line: int
    operator: str
    description: str
    start: int
    end: int
    replacement: str

    @property
    def key(self):
        return f"{self.file}:{self.line}:{self.operator}:{self.start}"

    def apply(self, source):
        return source[: self.start] + self.replacement + source[self.end :]


class Generator(ast.NodeVisitor):
    def __init__(self, file, source):
        self.file = file
        self.source = source
        self.lines = source.splitlines(keepends=True)
        self.offsets = [0]
        for line in self.lines:
            self.offsets.append(self.offsets[-1] + len(line.encode("utf-8")))
        self.encoded = source.encode("utf-8")
        self.mutants = []
        self.function_depth = 0
        self.annotation_nodes = set()

    def offset(self, line, column):
        return self.offsets[line - 1] + column

    def text(self, node):
        start = self.offset(node.lineno, node.col_offset)
        end = self.offset(node.end_lineno, node.end_col_offset)
        return self.encoded[start:end].decode("utf-8")

    def add(self, node, operator, description, replacement, start=None, end=None):
        start = self.offset(node.lineno, node.col_offset) if start is None else start
        end = self.offset(node.end_lineno, node.end_col_offset) if end is None else end
        char_start = len(self.encoded[:start].decode("utf-8"))
        char_end = len(self.encoded[:end].decode("utf-8"))
        self.mutants.append(
            Mutant(
                self.file,
                node.lineno,
                operator,
                description,
                char_start,
                char_end,
                replacement,
            )
        )

    def visit_FunctionDef(self, node):
        for argument in node.args.args + node.args.kwonlyargs:
            if argument.annotation is not None:
                self.mark(argument.annotation)
        if node.returns is not None:
            self.mark(node.returns)
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            self.visit(default)
        self.function_depth += 1
        for statement in node.body:
            self.visit(statement)
        self.function_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def mark(self, node):
        for child in ast.walk(node):
            self.annotation_nodes.add(id(child))

    def visit_AnnAssign(self, node):
        self.mark(node.annotation)
        if node.value is not None:
            self.statement(node)
            self.visit(node.value)

    def visit_Assign(self, node):
        self.statement(node)
        self.visit(node.value)

    def visit_AugAssign(self, node):
        self.statement(node)
        self.visit(node.value)

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant):
            return
        self.statement(node)
        self.visit(node.value)

    def statement(self, node):
        if self.function_depth == 0:
            return
        self.add(
            node, "delete", f"delete `{self.text(node).splitlines()[0][:60]}`", "pass"
        )

    def visit_Return(self, node):
        if node.value is not None:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                self.add(
                    node.value,
                    "return_none",
                    f"return None in place of `{self.text(node.value).splitlines()[0][:50]}`",
                    "None",
                )
            self.visit(node.value)

    def visit_Raise(self, node):
        if self.function_depth:
            self.add(node, "delete_raise", "delete a raise", "pass")

    def visit_Continue(self, node):
        self.add(node, "continue_to_break", "break in place of continue", "break")

    def visit_Break(self, node):
        self.add(node, "break_to_continue", "continue in place of break", "continue")

    def condition(self, test):
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            return
        self.add(
            test,
            "negate",
            f"negate `{self.text(test).splitlines()[0][:60]}`",
            f"(not ({self.text(test)}))",
        )

    def visit_If(self, node):
        self.condition(node.test)
        self.generic_visit(node)

    def visit_While(self, node):
        if not (isinstance(node.test, ast.Constant) and node.test.value is True):
            self.condition(node.test)
        self.generic_visit(node)

    def visit_IfExp(self, node):
        self.condition(node.test)
        self.generic_visit(node)

    def visit_Compare(self, node):
        if len(node.ops) == 1:
            operator = node.ops[0]
            replacement = COMPARE.get(type(operator))
            if replacement:
                left_end = self.offset(node.left.end_lineno, node.left.end_col_offset)
                right_start = self.offset(
                    node.comparators[0].lineno, node.comparators[0].col_offset
                )
                between = self.encoded[left_end:right_start].decode("utf-8")
                old = COMPARE_TEXT[type(operator)]
                if between.count(old) >= 1 and between.strip() == old:
                    self.add(
                        node,
                        "compare",
                        f"`{old}` to `{replacement}`",
                        between.replace(old, replacement),
                        start=left_end,
                        end=right_start,
                    )
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        old = "and" if isinstance(node.op, ast.And) else "or"
        new = "or" if old == "and" else "and"
        first, second = node.values[0], node.values[1]
        left_end = self.offset(first.end_lineno, first.end_col_offset)
        right_start = self.offset(second.lineno, second.col_offset)
        between = self.encoded[left_end:right_start].decode("utf-8")
        if between.strip(" \n()") == old:
            self.add(
                node,
                "boolean",
                f"`{old}` to `{new}`",
                between.replace(old, new, 1),
                start=left_end,
                end=right_start,
            )
        self.generic_visit(node)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            self.add(node, "remove_not", "remove `not`", f"({self.text(node.operand)})")
        self.generic_visit(node)

    def visit_BinOp(self, node):
        replacement = ARITHMETIC.get(type(node.op))
        if replacement and id(node) not in self.annotation_nodes:
            left_end = self.offset(node.left.end_lineno, node.left.end_col_offset)
            right_start = self.offset(node.right.lineno, node.right.col_offset)
            between = self.encoded[left_end:right_start].decode("utf-8")
            old = ARITHMETIC_TEXT[type(node.op)]
            if between.strip() == old and not self.string_operands(node):
                self.add(
                    node,
                    "arithmetic",
                    f"`{old}` to `{replacement}`",
                    between.replace(old, replacement),
                    start=left_end,
                    end=right_start,
                )
        self.generic_visit(node)

    def string_operands(self, node):
        return any(
            isinstance(side, (ast.JoinedStr, ast.Constant))
            and isinstance(getattr(side, "value", ""), str)
            or isinstance(side, ast.JoinedStr)
            for side in (node.left, node.right)
        )

    def visit_Constant(self, node):
        if id(node) in self.annotation_nodes:
            return
        value = node.value
        if value is True or value is False:
            self.add(
                node, "boolean_literal", f"`{value}` to `{not value}`", str(not value)
            )
        elif isinstance(value, int):
            self.add(
                node, "number", f"`{self.text(node)}` to `{value + 1}`", str(value + 1)
            )
        elif isinstance(value, float):
            self.add(
                node,
                "number",
                f"`{self.text(node)}` to `{value + 1.0}`",
                repr(value + 1.0),
            )

    def visit_JoinedStr(self, node):
        return


def generate(file, source):
    generator = Generator(file, source)
    generator.visit(ast.parse(source))
    unique = {}
    for mutant in generator.mutants:
        try:
            ast.parse(mutant.apply(source))
        except SyntaxError:
            continue
        unique.setdefault(mutant.key, mutant)
    return list(unique.values())
