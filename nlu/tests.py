"""Run with: venv/bin/python -m unittest nlu.tests   (no Django or database needed)."""
import unittest

from nlu.amounts import find_amounts, parse_amount, tokenize
from nlu.normalise import int_to_words, normalise_transcript


class AmountFormsTests(unittest.TestCase):
    def check(self, cases):
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_amount(text), expected)

    def test_standard_words(self):
        self.check({
            "send five thousand naira to tunde": 5_000,
            "send one thousand five hundred naira to kemi": 1_500,
            "pay one hundred and fifty thousand naira": 150_000,
            "transfer two hundred and fifty thousand naira to femi": 250_000,
            "send two million five hundred thousand naira to femi": 2_500_000,
            "buy hundred naira airtime": 100,
            "buy five hundred airtime": 500,
            "seventy five thousand naira": 75_000,
            "send a thousand naira": 1_000,
            "pay uche a hundred thousand naira": 100_000,
            "transfer a million naira to account": 1_000_000,
        })

    def test_k_and_milli(self):
        self.check({
            "abeg send five k give musa": 5_000,
            "send twenty five k give obinna": 25_000,
            "pay hundred k for dstv": 100_000,
            "help me send five hundred k to ibrahim": 500_000,
            "send one milli to ngozi": 1_000_000,
            "transfer one milli to musa": 1_000_000,
        })

    def test_decimals_and_half(self):
        self.check({
            "i want to send one point five k to femi": 1_500,
            "pay halima two point five k": 2_500,
            "abeg pay halima two point five million naira": 2_500_000,
            "i want to send half a million naira to halima": 500_000,
            "half a milli": 500_000,
        })

    def test_x_fifty_means_hundreds(self):
        self.check({
            "i want to send one fifty k to tunde": 150_000,
            "make you send two fifty k give blessing": 250_000,
            "yes i wan send one fifty thousand go segun": 150_000,
            "abeg send one hundred fifty k to segun": 150_000,
            "one twenty five k": 125_000,
        })

    def test_one_five_means_fifteen_hundred(self):
        self.check({
            "ask the app to send one five to zenith account three nine seven zero": 1_500,
            "yes na one five i bin wan send to funmi": 1_500,
            "stop sending that one five to the person": 1_500,
            "two five": 2_500,
        })

    def test_digits_from_asr(self):
        self.check({
            "send ₦5,000 to Tunde.": 5_000,
            "send 25 000 naira to adi": 25_000,
            "i beg send 150k to segun": 150_000,
            "pay 1.5k": 1_500,
            "send 2.5m to ngozi": 2_500_000,
            "send 500 k to brian": 500_000,
            "send 5,000": 5_000,
        })

    def test_account_numbers_are_not_amounts(self):
        self.check({
            "send five k to seven six four seven two five eight seven seven one in kuda": 5_000,
            "send five hundred to five eight nine eight three one nine four eight nine": 500,
            "send ten k go first bank four one three seven three three one five three one": 10_000,
            "account four one three six eight nine five three two one": None,
            "send one five two pampi accounts": None,
            "send it to 0123456789": None,
            "one thousand five hundred naira to uba account 76 24 72 49 64": 1_500,
            "send thank you go facebook 437 331 531": None,
        })
        self.assertEqual(
            parse_amount("send one hundred five eight nine eight three one nine four eight"), 100)

    def test_not_amounts(self):
        self.check({
            "": None, "yes": None, "make you stop": None,
            "show my last five transactions": None,
            "make i see that one": None,
            "pay a millionaire for my gotv subscription": None,
            "check my balance": None,
        })

    def test_nine_mobile_is_a_network(self):
        self.check({
            "buy two hundred naira nine mobile airtime": 200,
            "buy one thousand nine mobile airtime": 1_000,
            "buy 200 naira 9mobile airtime": 200,
        })

    def test_repeated_and_corrected_amounts(self):
        self.check({
            "help me buy one k airtime one k mtn airtime": 1_000,
            "send five k no ten k": 10_000,
            "let me send 500 k 3 brian": 500_000,       # the scaled amount beats a stray digit
            "send twenty five to ade": 25,              # what was said; no implicit thousand
        })
        self.assertEqual([a.value for a in find_amounts("one k one k")], [1_000, 1_000])

    def test_tokenize_keeps_account_digit_strings(self):
        self.assertEqual(tokenize("send 5,000 to 0123456789"), ["send", 5000.0, "to", "0123456789"])


class NormaliseTests(unittest.TestCase):
    def test_int_to_words_matches_the_read_prompts(self):
        self.assertEqual(int_to_words(1_500), "one thousand five hundred")
        self.assertEqual(int_to_words(150_000), "one hundred and fifty thousand")
        self.assertEqual(int_to_words(2_500_000), "two million five hundred thousand")
        self.assertEqual(int_to_words(100), "one hundred")

    def test_asr_output_is_brought_into_the_convention(self):
        cases = {
            "Abeg, send ₦5,000 to Tunde's GTB.": "abeg send five thousand naira to tunde's gtb",
            "Send 25 000 naira": "send twenty five thousand naira",
            "pay 1.5k now": "pay one point five k now",
            "i beg send 150k": "i beg send one hundred and fifty k",
            "buy 200 naira 9mobile airtime": "buy two hundred naira nine mobile airtime",
            "account 0123456789": "account zero one two three four five six seven eight nine",
            "account 76 24 72 49 64": "account seven six two four seven two four nine six four",
            "Twenty-five \"thousand\"": "twenty five thousand",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalise_transcript(raw), expected)

    def test_conventional_text_is_unchanged(self):
        for text in ("send one point five k to funmi's access account",
                     "abeg send two fifty k give blessing for opay", "no be am"):
            self.assertEqual(normalise_transcript(text), text)
        self.assertEqual(normalise_transcript("send  twenty k"), "send twenty k")

    def test_round_trip_through_the_parser(self):
        for n in (100, 500, 1_500, 25_000, 150_000, 250_000, 1_000_000, 2_500_000):
            self.assertEqual(parse_amount(normalise_transcript(f"send {n} naira")), n)


if __name__ == "__main__":
    unittest.main()
