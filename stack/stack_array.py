"""Stack implementation using arrays (Python list)."""


class StackA:
    def __init__(self, cap=100):
        self.items = []
        self.cap = cap

    def empty(self):
        return len(self.items) == 0

    def full(self):
        return len(self.items) >= self.cap

    def push(self, value):
        if self.full():
            print("Stack overflow!")
            return False
        self.items.append(value)
        print(f"Pushed: {value}")
        return True

    def pop(self):
        if self.empty():
            print("Stack underflow!")
            return None
        value = self.items.pop()
        print(f"Popped: {value}")
        return value

    def peek(self):
        if self.empty():
            print("Stack empty.")
            return None
        return self.items[-1]

    def show(self):
        if self.empty():
            print("Stack empty.")
        else:
            print("Stack (top -> bottom):", self.items[::-1])


def main():
    st = StackA(cap=10)

    while True:
        print("\n--- Array Stack Menu ---")
        print("1. Push")
        print("2. Pop")
        print("3. Peek")
        print("4. Show")
        print("5. Exit")
        choice = input("Enter choice: ").strip()

        if choice == "1":
            value = input("Enter value: ").strip()
            st.push(value)
        elif choice == "2":
            st.pop()
        elif choice == "3":
            top = st.peek()
            if top is not None:
                print(f"Top: {top}")
        elif choice == "4":
            st.show()
        elif choice == "5":
            print("Exit.")
            break
        else:
            print("Invalid.")


if __name__ == "__main__":
    main()
