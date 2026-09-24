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
    ("ba_en_04", "english", "balance", "what is my balance"),
    ("ba_en_05", "english", "balance", "tell me my account balance"),
    ("ba_en_06", "english", "balance", "show me my balance"),
    ("ba_en_07", "english", "balance", "do i have money in my account"),
    ("ba_pd_01", "pidgin", "balance", "wetin remain for my account"),
    ("ba_pd_02", "pidgin", "balance", "how much i get for account"),
    ("ba_pd_03", "pidgin", "balance", "abeg check my balance"),
    ("ba_pd_04", "pidgin", "balance", "make i know how much dey my account"),
    ("ba_pd_05", "pidgin", "balance", "how much dey my account"),
    ("ba_pd_06", "pidgin", "balance", "abeg tell me wetin dey my account"),
    ("ba_pd_07", "pidgin", "balance", "i wan check my balance"),
    ("ba_pd_08", "pidgin", "balance", "how much i get sef"),
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
    ("hi_en_03", "english", "history", "show me my transaction history"),
    ("hi_en_04", "english", "history", "what did i spend this month"),
    ("hi_en_05", "english", "history", "show my recent transfers"),
    ("hi_en_06", "english", "history", "how much did i send last month"),
    ("hi_pd_01", "pidgin", "history", "show me wetin i don spend"),
    ("hi_pd_02", "pidgin", "history", "make i see my last transaction dem"),
    ("hi_pd_03", "pidgin", "history", "abeg show me the money wey i don send"),
    ("hi_pd_04", "pidgin", "history", "wetin i don spend this month"),
    ("hi_pd_05", "pidgin", "history", "make i see my transaction dem"),
    # --- confirm / cancel (the risk layer depends on these)
    # Deliberately dense: these are the highest-stakes utterances in the system.
    # Mishearing "no" as "yes" moves money, so the model needs wide lexical coverage.
    ("cf_en_01", "english", "confirm", "yes"),
    ("cf_en_02", "english", "confirm", "yes confirm"),
    ("cf_en_03", "english", "confirm", "that is correct"),
    ("cf_en_04", "english", "confirm", "go ahead"),
    ("cf_en_05", "english", "confirm", "yes send it"),
    ("cf_en_06", "english", "confirm", "confirm the transfer"),
    ("cf_en_07", "english", "confirm", "yes that is right"),
    ("cf_en_08", "english", "confirm", "ok send it now"),
    ("cf_en_09", "english", "confirm", "correct send it"),
    ("cf_en_10", "english", "confirm", "yes please go ahead"),
    ("cf_en_11", "english", "confirm", "approve it"),
    ("cf_en_12", "english", "confirm", "that is the correct amount"),
    ("cf_pd_01", "pidgin", "confirm", "yes na"),
    ("cf_pd_02", "pidgin", "confirm", "e correct send am"),
    ("cf_pd_03", "pidgin", "confirm", "send am"),
    ("cf_pd_04", "pidgin", "confirm", "oya send am"),
    ("cf_pd_05", "pidgin", "confirm", "na him be that"),
    ("cf_pd_06", "pidgin", "confirm", "e correct"),
    ("cf_pd_07", "pidgin", "confirm", "abeg send am"),
    ("cf_pd_08", "pidgin", "confirm", "make you send am"),
    ("cf_pd_09", "pidgin", "confirm", "e dey correct send am"),
    ("cf_pd_10", "pidgin", "confirm", "na so"),
    ("cf_pd_11", "pidgin", "confirm", "oya go ahead"),
    ("cf_pd_12", "pidgin", "confirm", "yes send am now"),
    ("cn_en_01", "english", "cancel", "no"),
    ("cn_en_02", "english", "cancel", "cancel the transaction"),
    ("cn_en_03", "english", "cancel", "no that is wrong"),
    ("cn_en_04", "english", "cancel", "stop"),
    ("cn_en_05", "english", "cancel", "cancel it"),
    ("cn_en_06", "english", "cancel", "do not send it"),
    ("cn_en_07", "english", "cancel", "stop the transfer"),
    ("cn_en_08", "english", "cancel", "no that is not the right amount"),
    ("cn_en_09", "english", "cancel", "wait stop it"),
    ("cn_en_10", "english", "cancel", "no i did not say that"),
    ("cn_en_11", "english", "cancel", "cancel please"),
    ("cn_en_12", "english", "cancel", "no that is the wrong person"),
    ("cn_pd_01", "pidgin", "cancel", "no be am"),
    ("cn_pd_02", "pidgin", "cancel", "cancel am no send am"),
    ("cn_pd_03", "pidgin", "cancel", "no send am"),
    ("cn_pd_04", "pidgin", "cancel", "stop am"),
    ("cn_pd_05", "pidgin", "cancel", "abeg cancel am"),
    ("cn_pd_06", "pidgin", "cancel", "e no correct"),
    ("cn_pd_07", "pidgin", "cancel", "no be that one"),
    ("cn_pd_08", "pidgin", "cancel", "leave am"),
    ("cn_pd_09", "pidgin", "cancel", "wait wait no send am"),
    ("cn_pd_10", "pidgin", "cancel", "no be so"),
    ("cn_pd_11", "pidgin", "cancel", "abeg stop am"),
    ("cn_pd_12", "pidgin", "cancel", "no be that amount"),
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
    ("el_hi_01", "history", "Ask the app to show you what you have spent recently."),
    ("el_cf_01", "confirm", "The app just read back your transfer correctly. Tell it to go ahead."),
    ("el_cf_02", "confirm", "The app says: send ₦{amount_fmt} to {name}. That is right — tell it to send."),
    ("el_cn_01", "cancel", "The app read back the wrong amount. Tell it to stop."),
    ("el_cn_02", "cancel", "The app is about to send ₦{amount_fmt} to the wrong person. Stop it."),
]

DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
