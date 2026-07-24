"""Stack implementation using linked lists."""


class Node:
    def __init__(self, data):
        self.data = data
        self.next = None


class LinkedListStack:
    def __init__(self):
        self.top = None
        self.size = 0

    def is_empty(self):
        return self.top is None

    def push(self, value):
        new_node = Node(value)
        new_node.next = self.top
        self.top = new_node
        self.size += 1
        print(f"Pushed: {value}")

    def pop(self):
        if self.is_empty():
            print("Stack underflow! Cannot pop.")
            return None
        value = self.top.data
        self.top = self.top.next
        self.size -= 1
        print(f"Popped: {value}")
        return value

    def peek(self):
        if self.is_empty():
            print("Stack is empty. Nothing to peek.")
            return None
        return self.top.data

    def display(self):
        if self.is_empty():
            print("Stack is empty.")
            return

        elements = []
        current = self.top
        while current:
            elements.append(str(current.data))
            current = current.next
        print("Stack (top -> bottom):", " -> ".join(elements))


def main():
    stack = LinkedListStack()

    while True:
        print("\n--- Linked List Stack Menu ---")
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
