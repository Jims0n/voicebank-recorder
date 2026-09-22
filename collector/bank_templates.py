"""
Banking command templates for prompt generation.

LABELLING CONVENTION (see LABELLING.md). Every transcript is:
  - lowercase, no punctuation
  - numbers written as spoken words ("five thousand naira", "five k", "zero one two")
  - bank acronyms as lowercase letters joined ("gtb", "uba"); brand names as words ("opay")
  - Pidgin spelled with one fixed orthography (see PIDGIN_SPELLINGS in LABELLING.md)
The display text for READ prompts IS the transcript (capitalised for readability),
so the label is exact by construction.
"""

NAMES = ["Ade", "Chidi", "Musa", "Ngozi", "Tunde", "Aisha", "Emeka", "Funmi",
         "Ibrahim", "Kemi", "Obinna", "Zainab", "Segun", "Amaka", "Yusuf", "Bola",
         "Halima", "Uche", "Femi", "Blessing"]

# (canonical entity value, spoken form)
BANKS = [("GTB", "gtb"), ("Access", "access bank"), ("First Bank", "first bank"),
         ("Zenith", "zenith bank"), ("UBA", "uba"), ("Opay", "opay"),
         ("Moniepoint", "moniepoint"), ("Kuda", "kuda"), ("Palmpay", "palmpay"),
         ("Fidelity", "fidelity bank")]

NETWORKS = [("MTN", "mtn"), ("Airtel", "airtel"), ("Glo", "glo"), ("9mobile", "nine mobile")]
BILLERS = [("DSTV", "dstv"), ("GOtv", "gotv"), ("IKEDC", "ikedc"), ("EKEDC", "ekedc"),
           ("Startimes", "startimes")]

# (value in naira, [spoken forms]). Confusable pairs are deliberate:
# fifteen/fifty, 1,500/15,000, 5,000/50,000. These are what the risk layer must catch.
AMOUNTS = [
    (500, ["five hundred naira", "five hundred"]),
    (1000, ["one thousand naira", "a thousand naira", "one k"]),
    (1500, ["one thousand five hundred naira", "fifteen hundred naira", "one point five k"]),
    (2000, ["two thousand naira", "two k"]),
    (2500, ["two thousand five hundred naira", "two point five k"]),
    (5000, ["five thousand naira", "five k", "five thousand"]),
    (10000, ["ten thousand naira", "ten k"]),
    (15000, ["fifteen thousand naira", "fifteen k"]),
    (20000, ["twenty thousand naira", "twenty k"]),
    (25000, ["twenty five thousand naira", "twenty five k"]),
    (50000, ["fifty thousand naira", "fifty k"]),
    (75000, ["seventy five thousand naira", "seventy five k"]),
    (100000, ["one hundred thousand naira", "a hundred thousand naira", "hundred k"]),
    (150000, ["one hundred and fifty thousand naira", "one fifty k"]),
    (250000, ["two hundred and fifty thousand naira", "two fifty k"]),
    (500000, ["five hundred thousand naira", "half a million naira", "five hundred k"]),
    (1000000, ["one million naira", "a million naira", "one milli"]),
    (2500000, ["two point five million naira", "two million five hundred thousand naira"]),
]
AIRTIME_AMOUNTS = [(100, ["one hundred naira", "hundred naira"]),
                   (200, ["two hundred naira"]),
                   (500, ["five hundred naira", "five hundred"]),
                   (1000, ["one thousand naira", "one k"]),
                   (2000, ["two thousand naira", "two k"])]

# (template_id, language, intent, pattern). Slots: {amount} {name} {bank} {acct} {network} {biller}
READ_TEMPLATES = [
    # --- transfer, English
    ("tr_en_01", "english", "transfer", "send {amount} to {name}"),
    ("tr_en_02", "english", "transfer", "transfer {amount} to {name}"),
    ("tr_en_03", "english", "transfer", "i want to send {amount} to {name} at {bank}"),
    ("tr_en_04", "english", "transfer", "pay {name} {amount}"),
    ("tr_en_05", "english", "transfer", "transfer {amount} to account number {acct} {bank}"),
    ("tr_en_06", "english", "transfer", "send {amount} to {acct} in {bank}"),
    ("tr_en_07", "english", "transfer", "please send {name} {amount} now"),
    # --- transfer, Pidgin
    ("tr_pd_01", "pidgin", "transfer", "abeg send {amount} give {name}"),
    ("tr_pd_02", "pidgin", "transfer", "transfer {amount} give {name}"),
    ("tr_pd_03", "pidgin", "transfer", "i wan send {amount} to {name}"),
    ("tr_pd_04", "pidgin", "transfer", "send {amount} enter {name} account"),
    ("tr_pd_05", "pidgin", "transfer", "abeg pay {name} {amount}"),
    ("tr_pd_06", "pidgin", "transfer", "comot {amount} send am give {name}"),
    ("tr_pd_07", "pidgin", "transfer", "send {amount} go this account {acct} {bank}"),
    ("tr_pd_08", "pidgin", "transfer", "make you send {amount} give {name} for {bank}"),
    # --- balance
    ("ba_en_01", "english", "balance", "what is my account balance"),
    ("ba_en_02", "english", "balance", "check my balance"),
    ("ba_en_03", "english", "balance", "how much do i have in my account"),
    ("ba_pd_01", "pidgin", "balance", "wetin remain for my account"),
    ("ba_pd_02", "pidgin", "balance", "how much i get for account"),
    ("ba_pd_03", "pidgin", "balance", "abeg check my balance"),
    ("ba_pd_04", "pidgin", "balance", "make i know how much dey my account"),
    # --- airtime
    ("ai_en_01", "english", "airtime", "buy {amount} airtime"),
    ("ai_en_02", "english", "airtime", "recharge my phone with {amount}"),
    ("ai_en_03", "english", "airtime", "buy {amount} {network} airtime for {name}"),
    ("ai_pd_01", "pidgin", "airtime", "buy {amount} card for my line"),
    ("ai_pd_02", "pidgin", "airtime", "abeg recharge my phone {amount}"),
    ("ai_pd_03", "pidgin", "airtime", "load {amount} airtime for my {network} line"),
    # --- bills
    ("bi_en_01", "english", "bill", "pay my {biller} bill"),
    ("bi_en_02", "english", "bill", "pay {amount} for {biller}"),
    ("bi_pd_01", "pidgin", "bill", "abeg pay my {biller}"),
    ("bi_pd_02", "pidgin", "bill", "i wan pay {biller} {amount}"),
    # --- history
    ("hi_en_01", "english", "history", "show my last five transactions"),
    ("hi_en_02", "english", "history", "what did i spend last week"),
    ("hi_pd_01", "pidgin", "history", "show me wetin i don spend"),
    ("hi_pd_02", "pidgin", "history", "make i see my last transaction them"),
    # --- confirm / cancel (the risk layer depends on these)
    ("cf_en_01", "english", "confirm", "yes"),
    ("cf_en_02", "english", "confirm", "yes confirm"),
    ("cf_en_03", "english", "confirm", "that is correct"),
    ("cf_pd_01", "pidgin", "confirm", "yes na"),
    ("cf_pd_02", "pidgin", "confirm", "e correct send am"),
    ("cn_en_01", "english", "cancel", "no"),
    ("cn_en_02", "english", "cancel", "cancel the transaction"),
    ("cn_en_03", "english", "cancel", "no that is wrong"),
    ("cn_pd_01", "pidgin", "cancel", "no be am"),
    ("cn_pd_02", "pidgin", "cancel", "cancel am no send am"),
]

# Elicited prompts: speaker sees a task, says it their own way. Transcript is blank
# (transcribe later), but the ground-truth entities are known, so amount accuracy
# can be measured on natural speech.
ELICITED_TEMPLATES = [
    ("el_tr_01", "transfer", "Ask the app to send ₦{amount_fmt} to {name}."),
    ("el_tr_02", "transfer", "Ask the app to send ₦{amount_fmt} to {name}'s {bank} account."),
    ("el_tr_03", "transfer", "Ask the app to send ₦{amount_fmt} to account {acct_digits} ({bank})."),
    ("el_ai_01", "airtime", "Ask the app to buy ₦{amount_fmt} {network} airtime."),
    ("el_bi_01", "bill", "Ask the app to pay ₦{amount_fmt} for your {biller} subscription."),
    ("el_ba_01", "balance", "Ask the app how much money is in your account."),
    ("el_cf_01", "confirm", "The app just read back your transfer correctly. Tell it to go ahead."),
    ("el_cn_01", "cancel", "The app read back the wrong amount. Tell it to stop."),
]

DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
