"""Like stub_deck, but the model returns defective cards so the quality gate blocks export."""
import stub_deck

stub_deck.BAD = True
install = stub_deck.install
