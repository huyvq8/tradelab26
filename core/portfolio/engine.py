
class Portfolio:

    def __init__(self):
        self.balance = 1000
        self.positions = []

    def summary(self):
        return {
            "balance": self.balance,
            "open_positions": len(self.positions)
        }
