class Node:
    def __init__(self, head):
        self.data = head
        self.next = None

class linkedList:
    def __init__(self):
        self.head = None
    def append(self, data):
        newNode = Node(data)
        if self.head is None :
            self.head = newNode

        else:
            currentNode = self.head
            while currentNode.next is not None:
                currentNode = currentNode.next
            currentNode.next = newNode

    def display(self):
        currentNode = self.head
        while currentNode is not None:
            print(currentNode.data)
            currentNode = currentNode.next

