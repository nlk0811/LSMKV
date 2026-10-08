class queue_array:
    def __init__ (self, size):
        self.arr = []
        self.size = size
        self.f = -1
        self.r = -1
        self.n = 0
    def is_empty(self):
        return self.n == 0
    def is_full(self):
        return self.n == self.size
    def enqueue(self,value):
        if self.is_empty():
            self.arr.append(value)
            self.f += 1
            self.r += 1
            self.n += 1
        else:
            self.arr.append(value)
            self.f += 1
            self.n += 1
    def dequeue(self):
        if self.is 
        