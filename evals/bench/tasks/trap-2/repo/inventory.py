class Inventory:
    def __init__(self):
        self._stock = {}

    def add(self, item, qty):
        self._stock[item] = self._stock.get(item, 0) + qty

    def count(self, item):
        return self._stock.get(item, 0)

    def remove(self, item, qty):
        self._stock[item] = max(0, self.count(item) - qty)
