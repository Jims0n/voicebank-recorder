# Labelling convention

Fix this before the first recording. Changing it later means relabelling everything.

## Transcripts
- Lowercase, no punctuation.
- Numbers as spoken words, exactly as said: `five thousand naira`, `five k`, `one point five k`.
  Never digits. The ASR learns speech → words; turning words into ₦ amounts is the NLU's job.
- Account numbers digit by digit. If the speaker says "oh" for zero, write `oh`; the normaliser maps it.
- Bank acronyms joined, lowercase: `gtb`, `uba`. Brand names as words: `opay`, `access bank`.
- Hesitations (`em`, `ehn`) are transcribed only in elicited clips, and kept as-is.

## Pidgin spelling (one form per word)
abeg · wetin · dey · don · wan · am · na · comot · una · sef · oya · make (not "mek") ·
dem (not "them" when it's the Pidgin plural marker) · e (it/he/she) · no be · wey (not "we") ·
na him · na so · that one

Add to this list as new words appear, and never spell the same word two ways.

## Confirm and cancel
Treat these as the highest-stakes labels in the set. A confirm mislabelled as a cancel
(or the reverse) teaches the model to move money against the speaker's intent. When in
doubt about a confirm/cancel clip, reject it rather than guess.

## Elicited clips
Transcribe verbatim in the admin (`transcript_override`). Fast method: run Whisper-large
on them for a draft, then correct by ear. The ground-truth amount/recipient is already
stored on the prompt, so amount accuracy can be scored even before transcription.

## Review rules
Reject if: wrong prompt read, cut off mid-word, unintelligible, someone else talking over.
If the speaker changed a word but it's still a valid command, APPROVE and put what they
actually said in `transcript_override`. That's free natural variation.
