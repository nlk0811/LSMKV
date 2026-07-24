"""Stack implementation using arrays (Python list)."""


class ArrayStack:
    def __init__(self, capacity=100):
        self.stack = []
        self.capacity = capacity

    def is_empty(self):
        return len(self.stack) == 0

    def is_full(self):
        return len(self.stack) >= self.capacity

    def push(self, value):
        if self.is_full():
            print("Stack overflow! Cannot push.")
            return False
        self.stack.append(value)
        print(f"Pushed: {value}")
        return True

    def pop(self):
        if self.is_empty():
            print("Stack underflow! Cannot pop.")
            return None
        value = self.stack.pop()
        print(f"Popped: {value}")
        return value

    def peek(self):
        if self.is_empty():
            print("Stack is empty. Nothing to peek.")
            return None
        return self.stack[-1]

    def display(self):
        if self.is_empty():
            print("Stack is empty.")
        else:
            print("Stack (top -> bottom):", self.stack[::-1])


def main():
    stack = ArrayStack(capacity=10)

    while True:
        print("\n--- Array Stack Menu ---")
        print("1. Push")
        print("2. Pop")
        print("3. Peek")
        print("4. Display")
        print("5. Exit")
        choice = input("Enter choice: ").strip()

        if choice == "1":
            value = input("Enter value to push: ").strip()
            stack.push(value)
        elif choice == "2":
            stack.pop()
        elif choice == "3":
            top = stack.peek()
            if top is not None:
                print(f"Top element: {top}")
        elif choice == "4":
            stack.display()
        elif choice == "5":
            print("Exiting.")
            break
        else:
            print("Invalid choice. Try again.")


if __name__ == "__main__":
    main()
