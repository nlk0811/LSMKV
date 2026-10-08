"""Evaluate a postfix expression using a stack."""


def eval_post(expr):
    s = []
    toks = expr.split()

    for t in toks:
        if is_num(t):
            s.append(float(t))
        else:
            if len(s) < 2:
                raise ValueError("Invalid postfix.")

            b = s.pop()
            a = s.pop()

            if t == "+":
                s.append(a + b)
            elif t == "-":
                s.append(a - b)
            elif t == "*":
                s.append(a * b)
            elif t == "/":
                if b == 0:
                    raise ZeroDivisionError("Division by zero.")
                s.append(a / b)
            elif t == "^":
                s.append(a ** b)
            else:
                raise ValueError(f"Unknown op: {t}")

    if len(s) != 1:
        raise ValueError("Invalid postfix.")

    res = s[0]
    return int(res) if res.is_integer() else res


def is_num(t):
    try:
        float(t)
        return True
    except ValueError:
        return False


def main():
    print("Postfix Expression Evaluator")
    print("Supports +, -, *, /, ^ and multi-digit numbers.")
    print("Enter expressions (empty line to quit).\n")

    while True:
        expr = input("Postfix expression: ").strip()
        if not expr:
            break

        try:
            result = eval_post(expr)
            print(f"Result: {result}\n")
        except (ValueError, ZeroDivisionError) as error:
            print(f"Error: {error}\n")


if __name__ == "__main__":
    main()
