"""Queue implementation using arrays (circular array)."""


class QueueA:
    def __init__(self, cap=10):
        self.arr = [None] * cap
        self.cap = cap
        self.f = -1
        self.r = -1
        self.n = 0

    def empty(self):
        return self.n == 0

    def full(self):
        return self.n == self.cap

    def enq(self, value):
        if self.full():
            print("Queue full!")
            return False

        if self.empty():
            self.f = 0

        self.r = (self.r + 1) % self.cap
        self.arr[self.r] = value
        self.n += 1
        print(f"Enqueued: {value}")
        return True

    def deq(self):
        if self.empty():
            print("Queue empty!")
            return None

        value = self.arr[self.f]
        self.arr[self.f] = None

        if self.n == 1:
            self.f = -1
            self.r = -1
        else:
            self.f = (self.f + 1) % self.cap

        self.n -= 1
        print(f"Dequeued: {value}")
        return value

    def front(self):
        if self.empty():
            print("Queue empty.")
            return None
        return self.arr[self.f]

    def rear(self):
        if self.empty():
            print("Queue empty.")
            return None
        return self.arr[self.r]

    def show(self):
        if self.empty():
            print("Queue empty.")
            return

        elems = []
        idx = self.f
        for _ in range(self.n):
            elems.append(str(self.arr[idx]))
            idx = (idx + 1) % self.cap
        print("Queue (front -> rear):", " <- ".join(elems))


def main():
    q = QueueA(cap=5)

    while True:
        print("\n--- Array Queue Menu ---")
        print("1. Enq")
        print("2. Deq")
        print("3. Front")
        print("4. Rear")
        print("5. Show")
        print("6. Exit")
        choice = input("Enter choice: ").strip()

        if choice == "1":
            value = input("Enter value: ").strip()
            q.enq(value)
        elif choice == "2":
            q.deq()
        elif choice == "3":
            e = q.front()
            if e is not None:
                print(f"Front: {e}")
        elif choice == "4":
            e = q.rear()
            if e is not None:
                print(f"Rear: {e}")
        elif choice == "5":
            q.show()
        elif choice == "6":
            print("Exit.")
            break
        else:
            print("Invalid.")


if __name__ == "__main__":
    main()
