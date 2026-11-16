"""Informe legible de anomalias de auto-apply-rfef."""
import unittest

import support  # noqa: F401  # debe ir antes que los modulos de scripts: anade scripts/ al sys.path
import anomaly_report as report


class ExplainTests(unittest.TestCase):
    def text(self, lines):
        return "\n".join(report.explain(lines))

    def test_table_differences_are_grouped_by_cause_and_name_teams_in_plain_words(self) -> None:
        out = self.text(["anomalia [tabla] t01 casa gf: esperado=3 obtenido=9", "anomalia [tabla] t01 pt: esperado=4 obtenido=6",
                         "anomalia [tabla] t02 fuera pp: esperado=0 obtenido=1"])
        self.assertIn("3 diferencia(s) en 1 comprobación(es)", out)
        self.assertIn("La clasificación de la RFEF no cuadra con los resultados (3)", out)
        self.assertIn("Equipos afectados (2): t01, t02", out)
        self.assertIn("t01, en casa, goles a favor: según los partidos del feed 3, según la tabla de la RFEF 9", out)
        self.assertIn("t01, en total, puntos: según los partidos del feed 4, según la tabla de la RFEF 6", out)
        self.assertIn("Qué hacer:", out)

    def test_original_lines_are_kept_as_technical_detail(self) -> None:
        line = "anomalia [par-invertido] a - b: esperado=b - a (feed) obtenido=a - b (RFEF)"
        out = self.text([line])
        self.assertIn("Local y visitante invertidos", out)
        self.assertIn("feed: b - a; RFEF: a - b", out)
        self.assertTrue(out.rstrip().endswith(line))

    def test_unknown_rule_and_free_text_are_never_lost(self) -> None:
        out = self.text(["anomalia [regla-nueva] x: esperado=1 obtenido=2", "texto libre"])
        self.assertIn("Comprobación no clasificada", out)
        self.assertIn("texto libre", out)

    def test_many_cases_are_truncated_but_counted(self) -> None:
        out = self.text(["anomalia [tabla] t%02d pt: esperado=1 obtenido=2" % i for i in range(12)])
        self.assertIn("y %d más" % (12 - report.MAX_EXAMPLES), out)
        self.assertIn("(12)", out)

    def test_note_lines_are_shown_as_note_not_counted_as_difference(self) -> None:
        out = self.text(["anomalia [tabla] t01 pt: esperado=1 obtenido=2", "nota: cuidado con Clausura"])
        self.assertIn("1 diferencia(s)", out)
        self.assertIn("nota: cuidado con Clausura", out)


if __name__ == "__main__":
    unittest.main()
