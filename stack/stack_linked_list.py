"""Stack implementation using linked lists."""


class N:
    def __init__(self, d):
        self.d = d
        self.nxt = None


class StackL:
    def __init__(self):
        self.head = None
        self.count = 0

    def empty(self):
        return self.head is None

    def push(self, value):
        node = N(value)
        node.nxt = self.head
        self.head = node
        self.count += 1
        print(f"Pushed: {value}")

    def pop(self):
        if self.empty():
            print("Stack underflow!")
            return None
        value = self.head.d
        self.head = self.head.nxt
        self.count -= 1
        print(f"Popped: {value}")
        return value

    def peek(self):
        if self.empty():
            print("Stack empty.")
            return None
        return self.head.d

    def show(self):
        if self.empty():
            print("Stack empty.")
            return

        elems = []
        cur = self.head
        while cur:
            elems.append(str(cur.d))
            cur = cur.nxt
        print("Stack (top -> bottom):", " -> ".join(elems))


def main():
    st = StackL()

    while True:
        print("\n--- Linked List Stack Menu ---")
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
