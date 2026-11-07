"""Run every example; fail on any error note."""
import glob
import os
import unittest

from tzel import Session

HERE = os.path.dirname(os.path.abspath(__file__))


class TestExamples(unittest.TestCase):
    def test_examples_run_clean(self):
        for path in glob.glob(os.path.join(HERE, "..", "examples", "*.tz")):
            with self.subTest(path=os.path.basename(path)):
                s = Session()
                with open(path, encoding="utf-8") as fh:
                    results = s.run_source(fh.read())
                errors = [(ln, n) for ln, notes in results for n in notes
                          if n.startswith(("error", "internal error"))]
                self.assertEqual(errors, [])

    def test_hexagon_closes_exactly(self):
        s = Session()
        s.run_source("lattice hex\nh: →E ; →NE ; →NW ; →W ; →SW ; →SE\n")
        self.assertTrue(s.shapes["h"].closed)

    def test_free_word_order(self):
        a, b = Session(), Session()
        a.run_source("p: arc ⌒90° →NE×2\n")
        b.run_source("p: →NE×2 arc ⌒90°\n")
        self.assertEqual(a.world(a.shapes["p"]), b.world(b.shapes["p"]))

    def test_own_anchor_transforms_commute(self):
        a, b = Session(), Session()
        a.run_source("r: rect w:2 h:1\nturn r 30\nmove r →E×2\n")
        b.run_source("r: rect w:2 h:1\nmove r →E×2\nturn r 30\n")
        pa, pb = a.world(a.shapes["r"]), b.world(b.shapes["r"])
        for p, q in zip(pa, pb):
            self.assertAlmostEqual(p[0], q[0])
            self.assertAlmostEqual(p[1], q[1])

    def test_revision_moves_dependents(self):
        s = Session()
        s.run_source("stem: line ∠90° ⟷2\nbud: circle r:1 at stem.end\n")
        y0 = s.anchor("bud", "center", None)[1]
        s.run_source("stem ⟷4\n")
        self.assertAlmostEqual(s.anchor("bud", "center", None)[1] - y0, 2.0)


if __name__ == "__main__":
    unittest.main()
