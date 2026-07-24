"""Convert an infix expression to postfix using a stack."""

PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2, "^": 3}
RIGHT_ASSOCIATIVE = {"^"}


def infix_to_postfix(expression):
    output = []
    operator_stack = []

    tokens = tokenize(expression)

    for token in tokens:
        if is_operand(token):
            output.append(token)
        elif token == "(":
            operator_stack.append(token)
        elif token == ")":
            while operator_stack and operator_stack[-1] != "(":
                output.append(operator_stack.pop())
            if not operator_stack:
                raise ValueError("Mismatched parentheses in expression.")
            operator_stack.pop()
        elif token in PRECEDENCE:
            while (
                operator_stack
                and operator_stack[-1] != "("
                and (
                    PRECEDENCE[operator_stack[-1]] > PRECEDENCE[token]
                    or (
                        PRECEDENCE[operator_stack[-1]] == PRECEDENCE[token]
                        and token not in RIGHT_ASSOCIATIVE
                    )
                )
            ):
                output.append(operator_stack.pop())
            operator_stack.append(token)
        else:
            raise ValueError(f"Invalid token: {token}")

    while operator_stack:
        if operator_stack[-1] in "()":
            raise ValueError("Mismatched parentheses in expression.")
        output.append(operator_stack.pop())

    return " ".join(output)


def is_operand(token):
    if token.isdigit():
        return True
    if len(token) > 1 and token.replace(".", "", 1).isdigit():
        return True
    return token.isalpha()


def tokenize(expression):
    tokens = []
    operand = ""

    for char in expression.replace(" ", ""):
        if char.isalnum() or char == ".":
            operand += char
        else:
            if operand:
                tokens.append(operand)
                operand = ""
            tokens.append(char)

    if operand:
        tokens.append(operand)

    return tokens


def main():
    print("Infix to Postfix Converter")
    print("Supports +, -, *, /, ^ and multi-digit numbers.")
    print("Enter expressions (empty line to quit).\n")

    while True:
        expression = input("Infix expression: ").strip()
        if not expression:
            break

        try:
            postfix = infix_to_postfix(expression)
            print(f"Postfix: {postfix}\n")
        except ValueError as error:
            print(f"Error: {error}\n")


if __name__ == "__main__":
    main()
