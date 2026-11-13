"""Run with: venv/bin/python -m unittest scripts.test_evaluate   (no model, Django or ffmpeg)."""
import io
import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from scripts import evaluate as ev


def row(text, speaker="SPK1", **kw):
    return {"audio": f"audio/{speaker}/{abs(hash((text, speaker, str(kw))))}.wav", "text": text,
            "speaker": speaker, "gender": "female", "first_language": "yoruba",
            "spoken_language": "pidgin", "instruction_read": False, "usable_for_intent": True,
            "intent": "transfer", "mode": "read", "entities": {}, **kw}


class AlignmentTests(unittest.TestCase):
    def test_counts_substitutions_deletions_insertions(self):
        ops = ev.align("send five k give tunde".split(), "send fine k to give tunde now".split())
        self.assertEqual(ev.error_counts(ops), {"sub": 1, "del": 0, "ins": 2})
        ops = ev.align("abeg send am".split(), "send".split())
        self.assertEqual(ev.error_counts(ops), {"sub": 0, "del": 2, "ins": 0})

    def test_empty_sides(self):
        self.assertEqual(ev.error_counts(ev.align([], ["a", "b"])), {"sub": 0, "del": 0, "ins": 2})
        self.assertEqual(ev.align([], []), [])


class ClipScoringTests(unittest.TestCase):
    def test_wer_is_computed_after_normalising_digits(self):
        c = ev.score_clip(row("send five thousand naira to tunde"), "Send ₦5,000 to Tunde.")
        self.assertEqual(c["wer"], 0)
        self.assertEqual(c["hypothesis"], "send five thousand naira to tunde")
        self.assertEqual(c["hypothesis_raw"], "Send ₦5,000 to Tunde.")

    def test_amount_equivalent_forms_match(self):
        c = ev.score_clip(row("send five k give tunde", entities={"amount": 5000}),
                          "send five thousand give tunde")
        self.assertTrue(c["amount"]["scored"])
        self.assertTrue(c["amount"]["correct"])
        self.assertGreater(c["wer"], 0)          # different words, same money

    def test_amount_wrong_and_missing(self):
        wrong = ev.score_clip(row("send one five to bola", entities={"amount": 1500}), "send 152 bolas")
        self.assertEqual((wrong["amount"]["correct"], wrong["amount"]["missing"]), (False, False))
        missing = ev.score_clip(row("send five k", entities={"amount": 5000}), "send fight")
        self.assertEqual((missing["amount"]["correct"], missing["amount"]["missing"]), (False, True))

    def test_amount_scoring_rules(self):
        unspoken = ev.score_clip(row("yes", entities={"amount": 75000}), "yes")
        self.assertEqual(unspoken["amount"]["skip"], "amount not spoken")
        none = ev.score_clip(row("check my balance"), "check my balance")
        self.assertEqual(none["amount"]["skip"], "no amount in entities")
        read = ev.score_clip(row("the app is about to send five k", usable_for_intent=False,
                                 instruction_read=True, entities={"amount": 5000}), "x")
        self.assertEqual(read["amount"]["skip"], "instruction read")
        for c in (unspoken, none, read):
            self.assertFalse(c["amount"]["scored"])

    def test_recipient(self):
        hit = ev.score_clip(row("send five k to funmi's access account",
                                entities={"recipient": "Funmi"}), "send 5k to Funmi's access account")
        self.assertTrue(hit["recipient"]["correct"])
        miss = ev.score_clip(row("send money to ngozi", entities={"recipient": "Ngozi"}),
                             "send money to ingozi")
        self.assertFalse(miss["recipient"]["correct"])
        unsaid = ev.score_clip(row("yes", entities={"recipient": "Ngozi"}), "yes")
        self.assertEqual(unsaid["recipient"]["skip"], "recipient not spoken")


class AggregateTests(unittest.TestCase):
    def test_corpus_wer_and_breakdowns_carry_n(self):
        clips = [
            ev.score_clip(row("send five k", speaker="A", entities={"amount": 5000}), "send five k"),
            ev.score_clip(row("check my balance now", speaker="B", gender="male",
                              spoken_language="ambiguous"), "check balance"),
        ]
        res = ev.breakdown(clips)
        overall = res["overall"]
        self.assertEqual((overall["ref_words"], overall["del"]), (7, 2))
        self.assertAlmostEqual(overall["wer"], 2 / 7)
        self.assertEqual(overall["amount"], {"n": 1, "correct": 1, "missing": 0,
                                             "accuracy": 1.0, "missing_rate": 0.0})
        self.assertEqual(res["gender"]["male"]["clips"], 1)
        self.assertEqual(res["spoken_language"]["ambiguous"]["wer"], 0.5)
        self.assertIsNone(res["speaker"]["B"]["amount"]["accuracy"])   # n=0, not 0%
        self.assertIn("— (0)", ev.table(res, {"A": ("female", "yoruba"), "B": ("male", "igbo")}))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.rows = [row("abeg send five k give tunde", speaker="A",
                         entities={"amount": 5000, "recipient": "Tunde"}),
                     row("send one fifty k to halima", speaker="B", gender="male",
                         entities={"amount": 150000, "recipient": "Halima"})]
        manifest = self.tmp / "data" / "test.jsonl"
        manifest.parent.mkdir()
        manifest.write_text("".join(json.dumps(r) + "\n" for r in self.rows))
        for r in self.rows:
            wav = manifest.parent / r["audio"]
            wav.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(wav), "w") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\0\0" * 1600)
        self.manifest = manifest

    def args(self, *extra):
        return ev.parse_args([str(self.manifest), "--model", "fake", "--label", "base-1",
                              "--out", str(self.tmp / "results"), *extra])

    def fake_model(self, outputs):
        model = mock.Mock()
        model.transcribe.side_effect = lambda audio, **kw: (
            iter([mock.Mock(text=outputs.pop(0))]), None)
        return model

    def test_end_to_end_writes_json_dump_and_reuses_hypotheses(self):
        model = self.fake_model(["Abeg send 5k give Tunde.", "Send 150,000 to Alima"])
        with mock.patch.object(ev, "load_model", return_value=model) as load:
            payload = ev.run(self.args(), log=lambda *a: None)
        self.assertEqual(load.call_count, 1)
        out = self.tmp / "results"
        saved = json.loads((out / "base-1_test.json").read_text())
        self.assertEqual(saved["run"]["label"], "base-1")
        self.assertEqual(saved["run"]["decode"]["model"], "fake")
        overall = saved["results"]["overall"]
        self.assertEqual(overall["amount"]["correct"], 2)     # 5k == five k; 150,000 == one fifty k
        self.assertEqual(overall["recipient"], {"n": 2, "correct": 1, "accuracy": 0.5})
        self.assertEqual(set(saved["results"]["speaker"]), {"A", "B"})
        dump = (out / "base-1_test_clips.txt").read_text()
        self.assertIn("REF:", dump)
        self.assertIn("ALIMA", dump)                           # errors in caps
        self.assertEqual(len(payload["clips"]), 2)

        # Same model + settings: rescore from cache, never load the model.
        log = io.StringIO()
        with mock.patch.object(ev, "load_model") as load:
            again = ev.run(self.args(), log=lambda *a: print(*a, file=log))
        load.assert_not_called()
        self.assertIn("rescoring only", log.getvalue())
        self.assertEqual(again["results"], payload["results"])

        # Different decoding settings: decode again.
        with mock.patch.object(ev, "load_model", return_value=self.fake_model(["a", "b"])) as load:
            ev.run(self.args("--beam-size", "1"), log=lambda *a: None)
        load.assert_called_once()

    def test_label_must_be_filename_safe(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
            ev.parse_args([str(self.manifest), "--model", "m", "--label", "../oops"])

    def test_hugging_face_checkpoint_gets_conversion_hint(self):
        ckpt = self.tmp / "ckpt"
        ckpt.mkdir()
        (ckpt / "config.json").write_text("{}")
        with self.assertRaises(SystemExit) as e:
            ev.load_model(str(ckpt), "auto", "default")
        self.assertIn("ct2-transformers-converter", str(e.exception))


if __name__ == "__main__":
    unittest.main()
