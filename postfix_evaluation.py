"""Evaluate a postfix expression using a stack."""


def evaluate_postfix(expression):
    stack = []
    tokens = expression.split()

    for token in tokens:
        if is_number(token):
            stack.append(float(token))
        else:
            if len(stack) < 2:
                raise ValueError("Invalid postfix expression.")

            b = stack.pop()
            a = stack.pop()

            if token == "+":
                stack.append(a + b)
            elif token == "-":
                stack.append(a - b)
            elif token == "*":
                stack.append(a * b)
            elif token == "/":
                if b == 0:
                    raise ZeroDivisionError("Division by zero.")
                stack.append(a / b)
            elif token == "^":
                stack.append(a ** b)
            else:
                raise ValueError(f"Unknown operator: {token}")

    if len(stack) != 1:
        raise ValueError("Invalid postfix expression.")

    result = stack[0]
    return int(result) if result.is_integer() else result


def is_number(token):
    try:
        float(token)
        return True
    except ValueError:
        return False


def main():
    print("Postfix Expression Evaluator")
    print("Supports +, -, *, /, ^ and multi-digit numbers.")
    print("Enter expressions (empty line to quit).\n")

    while True:
        expression = input("Postfix expression: ").strip()
        if not expression:
            break

        try:
            result = evaluate_postfix(expression)
            print(f"Result: {result}\n")
        except (ValueError, ZeroDivisionError) as error:
            print(f"Error: {error}\n")


if __name__ == "__main__":
    main()
