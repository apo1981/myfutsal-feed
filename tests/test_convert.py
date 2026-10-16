"""US-007a2, criterios 1 a 5 y 7: conversor CSV -> documento de temporada de league-feed-v1.

Datos ficticios y siempre en directorios temporales: nada se escribe en el repositorio ni en `public/`.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import unittest
from typing import Any, Dict, List, Optional

import support
import convert_csv
from support import FEED, TempDirTestCase, output_of, run_script, tool_env

CONVERT = os.path.join(FEED, "scripts", "convert-csv")
VALIDATE = os.path.join(FEED, "scripts", "validate-schema")
CHECK_INVARIANTS = os.path.join(FEED, "scripts", "check-invariants")
EXAMPLES = os.path.join(FEED, "examples", "initial-load")
UPDATED_AT = "2026-10-31T20:00:00Z"
TEAM_COLUMNS = ["id", "name", "shortName"]
MATCH_COLUMNS = ["id", "phaseId", "phaseName", "phaseKind", "phaseOrder", "roundId", "roundNumber", "roundName",
                 "homeTeamId", "awayTeamId", "status", "date", "time", "homeScore", "awayScore", "decidedBy", "note"]
TEAMS = [["a", "Club A", "AAA"], ["b", "Club B", "BBB"], ["c", "Club C", "CCC"]]


def match_row(identifier: str = "m1", **changes: str) -> List[str]:
    row = {"id": identifier, "phaseId": "liga", "phaseName": "Liga", "phaseKind": "league", "phaseOrder": "1",
           "roundId": "liga-01", "roundNumber": "1", "roundName": "Jornada 1", "homeTeamId": "a", "awayTeamId": "b",
           "status": "scheduled", "date": "2026-11-07", "time": "18:00", "homeScore": "", "awayScore": "",
           "decidedBy": "", "note": ""}
    row.update(changes)
    return [row[name] for name in MATCH_COLUMNS]


def write_csv(path: str, header: List[str], rows: List[List[str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def read_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class ConverterTestCase(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.teams = os.path.join(self.tmp, "equipos.csv")
        self.matches = os.path.join(self.tmp, "partidos.csv")
        self.out_dir = os.path.join(self.tmp, "out")
        os.makedirs(self.out_dir)
        self.out = os.path.join(self.out_dir, "2026-27.json")

    def write_inputs(self, match_rows: List[List[str]], team_rows: Optional[List[List[str]]] = None) -> None:
        write_csv(self.teams, TEAM_COLUMNS, team_rows or TEAMS)
        write_csv(self.matches, MATCH_COLUMNS, match_rows)

    def convert(self, updated_at: str = UPDATED_AT, out: Optional[str] = None, teams: Optional[str] = None,
                matches: Optional[str] = None, env: Optional[Dict[str, str]] = None, script: bool = False):
        """Por defecto en el propio proceso (rapido); `script=True` ejecuta el punto de entrada `scripts/convert-csv`."""
        args = ["--teams", teams or self.teams, "--matches", matches or self.matches, "--season-id", "2026-27",
                "--competition-name", "Liga de prueba", "--updated-at", updated_at, "--out", out or self.out]
        if script:
            return run_script([CONVERT] + args, env=env)
        return support.run_main(convert_csv.main, ["--feed", FEED] + args)

    def converted(self, match_rows: List[List[str]]) -> Dict[str, Any]:
        self.write_inputs(match_rows)
        result = self.convert()
        self.assertEqual(result.returncode, 0, output_of(result))
        return read_json(self.out)

    def only_match(self, document: Dict[str, Any]) -> Dict[str, Any]:
        return document["phases"][0]["rounds"][0]["matches"][0]


class ValidConversion(ConverterTestCase):
    def test_us007a2_c1_example_csv_produce_a_document_that_passes_schema_and_invariants(self) -> None:
        result = self.convert(teams=os.path.join(EXAMPLES, "equipos.csv"), matches=os.path.join(EXAMPLES, "partidos.csv"), script=True)
        self.assertEqual(result.returncode, 0, output_of(result))
        validated = run_script([VALIDATE, self.out])
        self.assertEqual(validated.returncode, 0, output_of(validated))
        # Las invariantes se aplican sobre un public/ temporal, nunca sobre el real.
        public = support.build_public(self.tmp, read_json(self.out))
        invariants = run_script([CHECK_INVARIANTS, "--public", public])
        self.assertEqual(invariants.returncode, 0, output_of(invariants))

    def test_us007a2_c1_example_csv_cover_every_kind_of_row(self) -> None:
        self.convert(teams=os.path.join(EXAMPLES, "equipos.csv"), matches=os.path.join(EXAMPLES, "partidos.csv"))
        matches = [m for p in read_json(self.out)["phases"] for r in p["rounds"] for m in r["matches"]]
        self.assertEqual({m["status"] for m in matches}, {"finished", "scheduled", "postponed"})
        self.assertTrue(any(m["kickoff"]["time"] is None and m["kickoff"]["date"] for m in matches))
        self.assertTrue(any(m["kickoff"]["date"] is None for m in matches))

    def test_us007a2_c1_local_madrid_time_becomes_utc_in_winter_and_summer(self) -> None:
        winter = self.converted([match_row(date="2026-12-05", time="19:00")])
        self.assertEqual(self.only_match(winter)["kickoff"], {"date": "2026-12-05", "time": "2026-12-05T18:00:00Z"})
        summer = self.converted([match_row(date="2027-07-15", time="19:00")])
        self.assertEqual(self.only_match(summer)["kickoff"], {"date": "2027-07-15", "time": "2027-07-15T17:00:00Z"})

    def test_us007a2_c1_a_late_local_time_keeps_the_madrid_date(self) -> None:
        document = self.converted([match_row(date="2026-12-05", time="00:30")])
        self.assertEqual(self.only_match(document)["kickoff"], {"date": "2026-12-05", "time": "2026-12-04T23:30:00Z"})

    def test_us007a2_c1_score_decided_by_and_note_are_kept(self) -> None:
        document = self.converted([match_row(status="finished", homeScore="3", awayScore="3", decidedBy="penalties", note="Prorroga")])
        match = self.only_match(document)
        self.assertEqual((match["score"], match["decidedBy"], match["note"]), ({"home": 3, "away": 3}, "penalties", "Prorroga"))

    def test_us007a2_c1_phases_and_rounds_follow_the_order_of_the_file(self) -> None:
        document = self.converted([
            match_row("m1"), match_row("m2", homeTeamId="b", awayTeamId="c"),
            match_row("m3", roundId="liga-02", roundNumber="2", roundName="Jornada 2"),
            match_row("m4", phaseId="copa", phaseName="Copa", phaseKind="knockout", phaseOrder="2", roundId="copa-01", roundName="Final"),
        ])
        self.assertEqual([p["id"] for p in document["phases"]], ["liga", "copa"])
        self.assertEqual([r["id"] for r in document["phases"][0]["rounds"]], ["liga-01", "liga-02"])
        self.assertEqual([m["id"] for m in document["phases"][0]["rounds"][0]["matches"]], ["m1", "m2"])

    def test_us007a2_c1_crest_url_is_always_null_and_accents_are_kept(self) -> None:
        write_csv(self.teams, TEAM_COLUMNS, [["a", "Peñíscola Fútbol", "PEN"], ["b", "Club B", "BBB"]])
        write_csv(self.matches, MATCH_COLUMNS, [match_row()])
        self.assertEqual(self.convert().returncode, 0)
        self.assertEqual(read_json(self.out)["teams"][0], {"id": "a", "name": "Peñíscola Fútbol", "shortName": "PEN", "crestURL": None})

    def test_us007a2_c1_a_utf8_bom_and_blank_lines_are_tolerated(self) -> None:
        self.write_inputs([match_row()])
        with open(self.teams, encoding="utf-8") as handle:
            content = handle.read()
        with open(self.teams, "w", encoding="utf-8-sig") as handle:
            handle.write(content + "\n\n")
        self.assertEqual(self.convert().returncode, 0)


class EmptyFields(ConverterTestCase):
    def test_us007a2_c2_empty_time_gives_a_null_kickoff_time(self) -> None:
        match = self.only_match(self.converted([match_row(time="")]))
        self.assertEqual(match["kickoff"], {"date": "2026-11-07", "time": None})

    def test_us007a2_c2_empty_date_gives_a_null_kickoff_date(self) -> None:
        match = self.only_match(self.converted([match_row(date="", time="")]))
        self.assertEqual(match["kickoff"], {"date": None, "time": None})

    def test_us007a2_c2_empty_status_gives_scheduled(self) -> None:
        self.assertEqual(self.only_match(self.converted([match_row(status="")]))["status"], "scheduled")

    def test_us007a2_c2_the_three_empty_fields_together_in_three_rows(self) -> None:
        document = self.converted([match_row("m1", time=""), match_row("m2", date="", time="", homeTeamId="b", awayTeamId="c"),
                                   match_row("m3", status="", homeTeamId="c", awayTeamId="a")])
        matches = document["phases"][0]["rounds"][0]["matches"]
        self.assertEqual([m["kickoff"]["time"] is None for m in matches], [True, True, False])
        self.assertIsNone(matches[1]["kickoff"]["date"])
        self.assertEqual(matches[2]["status"], "scheduled")

    def test_us007a2_c2_empty_score_and_empty_optional_columns_give_nulls_never_zero_zero(self) -> None:
        match = self.only_match(self.converted([match_row()]))
        self.assertEqual((match["score"], match["decidedBy"], match["note"]), (None, None, None))


class WrongRows(ConverterTestCase):
    def assert_fails_at(self, rows: List[List[str]], source: str, line: int, text: str) -> None:
        self.write_inputs(rows)
        result = self.convert()
        self.assertNotEqual(result.returncode, 0, output_of(result))
        self.assertRegex(result.stderr, r"%s:%d: .*%s" % (re.escape(source), line, text))
        self.assertFalse(os.path.exists(self.out), "no debe haber salida parcial")
        self.assertEqual(os.listdir(self.out_dir), [], "no debe quedar ningun fichero temporal")

    def test_us007a2_c3_unknown_team_names_file_and_line(self) -> None:
        self.assert_fails_at([match_row("m1"), match_row("m2", awayTeamId="zzz")], "partidos.csv", 3, "equipo-inexistente")

    def test_us007a2_c3_repeated_match_id_names_file_and_line(self) -> None:
        self.assert_fails_at([match_row("m1"), match_row("m1", homeTeamId="b", awayTeamId="c")], "partidos.csv", 3, "repetido")

    def test_us007a2_c3_malformed_date_names_file_and_line(self) -> None:
        for bad in ("07/11/2026", "2026-13-01", "2026-02-30", "2026-1-5"):
            with self.subTest(bad):
                self.assert_fails_at([match_row(date=bad)], "partidos.csv", 2, "date")

    def test_us007a2_c3_unknown_status_names_file_and_line(self) -> None:
        self.assert_fails_at([match_row("m1"), match_row("m2", status="jugado", homeTeamId="b", awayTeamId="c")], "partidos.csv", 3, "status")

    def test_us007a2_s6_semicolon_separated_csv_names_the_cause(self) -> None:
        write_csv(self.teams, TEAM_COLUMNS, TEAMS)
        with open(self.matches, "w", encoding="utf-8") as handle:
            handle.write(";".join(MATCH_COLUMNS) + "\n")
        result = self.convert()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("la cabecera debe ser exactamente", result.stderr)
        self.assertIn("separador ';'", result.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_us007a2_s6_wrong_header_without_semicolons_has_no_separator_hint(self) -> None:
        write_csv(self.teams, TEAM_COLUMNS, TEAMS)
        with open(self.matches, "w", encoding="utf-8") as handle:
            handle.write("a,b,c\n")
        result = self.convert()
        self.assertIn("la cabecera debe ser exactamente", result.stderr)
        self.assertNotIn("separador", result.stderr)

    def test_us007a2_s5_example_notes_do_not_repeat_the_interface_text(self) -> None:
        with open(os.path.join(EXAMPLES, "partidos.csv"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("por confirmar", text.lower())

    def test_us007a2_c3_repeated_team_id_names_the_teams_file(self) -> None:
        write_csv(self.teams, TEAM_COLUMNS, TEAMS + [["a", "Otro", "OTR"]])
        write_csv(self.matches, MATCH_COLUMNS, [match_row()])
        result = self.convert()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("equipos.csv:5: id de equipo repetido", result.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_us007a2_c3_other_wrong_rows_also_fail_with_their_line(self) -> None:
        cases = [
            ("hora mal formada", match_row(time="25:00"), "time"),
            ("hora sin fecha", match_row(date="", time="18:00"), "date esta vacia"),
            ("hora inexistente (salto a verano)", match_row(date="2027-03-28", time="02:30"), "no existe"),
            ("hora ambigua (vuelta a invierno)", match_row(date="2026-10-25", time="02:30"), "ambigua"),
            ("marcador a medias", match_row(status="finished", homeScore="2"), "homeScore"),
            ("marcador negativo", match_row(status="finished", homeScore="-1", awayScore="0"), "homeScore"),
            ("finished sin marcador", match_row(status="finished"), "score-ausente-en-finished"),
            ("marcador con scheduled", match_row(homeScore="1", awayScore="0"), "score-con-estado-invalido"),
            ("equipo contra si mismo", match_row(awayTeamId="a"), "local-igual-visitante"),
            ("decidedBy desconocido", match_row(decidedBy="golden"), "decidedBy"),
            ("phaseKind desconocido", match_row(phaseKind="copa"), "phaseKind"),
            ("roundNumber cero", match_row(roundNumber="0"), "roundNumber"),
            ("id vacio", match_row(""), "id no puede estar vacia"),
        ]
        for label, row, text in cases:
            with self.subTest(label):
                self.assert_fails_at([row], "partidos.csv", 2, text)

    def test_us007a2_c3_inconsistent_phase_or_round_across_rows_fails_with_the_second_line(self) -> None:
        self.assert_fails_at([match_row("m1"), match_row("m2", homeTeamId="b", awayTeamId="c", phaseName="Otra")], "partidos.csv", 3, "fase")
        self.assert_fails_at([match_row("m1"), match_row("m2", homeTeamId="b", awayTeamId="c", roundName="Otra")], "partidos.csv", 3, "ronda")

    def test_us007a2_c3_wrong_header_and_wrong_column_count_fail(self) -> None:
        write_csv(self.teams, TEAM_COLUMNS, TEAMS)
        write_csv(self.matches, MATCH_COLUMNS[:-1], [match_row()[:-1]])
        result = self.convert()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("partidos.csv:1: la cabecera debe ser exactamente", result.stderr)
        write_csv(self.matches, MATCH_COLUMNS, [match_row()[:-2]])
        result = self.convert()
        self.assertIn("partidos.csv:2: tiene 15 columnas", result.stderr)

    def test_us007a2_c3_missing_input_file_fails_cleanly(self) -> None:
        self.write_inputs([match_row()])
        result = self.convert(matches=os.path.join(self.tmp, "no-existe.csv"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no-existe.csv:1", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_us007a2_c3_every_error_is_reported_not_only_the_first(self) -> None:
        self.write_inputs([match_row("m1", status="x"), match_row("m2", date="mal", homeTeamId="b", awayTeamId="c")])
        result = self.convert()
        self.assertIn("partidos.csv:2:", result.stderr)
        self.assertIn("partidos.csv:3:", result.stderr)

    def test_us007a2_c3_a_failed_run_leaves_an_existing_output_untouched(self) -> None:
        self.write_inputs([match_row()])
        self.assertEqual(self.convert().returncode, 0)
        before = sha256(self.out)
        self.write_inputs([match_row(status="mal")])
        self.assertNotEqual(self.convert().returncode, 0)
        self.assertEqual(sha256(self.out), before)
        self.assertEqual(os.listdir(self.out_dir), ["2026-27.json"])

    def test_us007a2_c3_invalid_parameters_fail_without_output(self) -> None:
        self.write_inputs([match_row()])
        for label, kwargs in (("updatedAt mal formado", {"updated_at": "ayer"}), ("updatedAt con zona", {"updated_at": "2026-10-31T20:00:00+01:00"})):
            with self.subTest(label):
                result = self.convert(**kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--updated-at", result.stderr)
                self.assertFalse(os.path.exists(self.out))


class Determinism(ConverterTestCase):
    def test_us007a2_c4_same_input_and_updated_at_give_identical_bytes(self) -> None:
        args = dict(teams=os.path.join(EXAMPLES, "equipos.csv"), matches=os.path.join(EXAMPLES, "partidos.csv"))
        first, second = os.path.join(self.out_dir, "1.json"), os.path.join(self.out_dir, "2.json")
        self.assertEqual(self.convert(out=first, **args).returncode, 0)
        self.assertEqual(self.convert(out=second, **args).returncode, 0)
        with open(first, "rb") as a, open(second, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_us007a2_c4_the_output_depends_on_the_given_instant_not_on_the_clock(self) -> None:
        self.write_inputs([match_row()])
        self.convert(updated_at="2020-01-01T00:00:00Z")
        self.assertEqual(read_json(self.out)["updatedAt"], "2020-01-01T00:00:00Z")
        self.convert(updated_at="2031-12-31T23:59:59Z")
        self.assertEqual(read_json(self.out)["updatedAt"], "2031-12-31T23:59:59Z")

    def test_us007a2_c4_the_output_ends_with_one_newline_and_uses_two_space_indent(self) -> None:
        self.write_inputs([match_row()])
        self.convert()
        with open(self.out, encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(text.endswith("}\n") and not text.endswith("\n\n"))
        self.assertIn('\n  "schemaVersion": 1,', text)


class NoNetwork(ConverterTestCase):
    def test_us007a2_c5_works_with_an_unreachable_proxy(self) -> None:
        self.write_inputs([match_row()])
        env = tool_env({"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""})
        result = self.convert(env=env, script=True)
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us007a2_c5_the_converter_imports_no_network_or_process_modules(self) -> None:
        with open(os.path.join(FEED, "scripts", "convert_csv.py"), encoding="utf-8") as handle:
            source = handle.read()
        imported = set(re.findall(r"^(?:import|from)\s+([a-zA-Z_]+)", source, re.MULTILINE))
        self.assertFalse(imported & {"socket", "ssl", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib"}, imported)


class PublicIsUntouched(ConverterTestCase):
    def snapshot(self, directory: str) -> List[str]:
        found = []
        for root, _dirs, names in os.walk(directory):
            found.extend((os.path.join(root, name), sha256(os.path.join(root, name))) for name in names)
        return sorted(found)

    def test_us007a2_c7_writing_into_public_is_refused_and_nothing_is_written(self) -> None:
        feed = os.path.join(self.tmp, "feed")
        shutil.copytree(os.path.join(FEED, "scripts"), os.path.join(feed, "scripts"), ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(feed, "public", "v1", "seasons"))
        self.write_inputs([match_row()])
        before = self.snapshot(feed)
        for target in ("public/v1/seasons/2026-27.json", "public/otro.json"):
            with self.subTest(target):
                result = run_script([os.path.join(feed, "scripts", "convert-csv"), "--teams", self.teams, "--matches", self.matches,
                                     "--season-id", "2026-27", "--competition-name", "x", "--updated-at", UPDATED_AT,
                                     "--out", os.path.join(feed, target)])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("public/", result.stderr)
        self.assertEqual(self.snapshot(feed), before)

    def test_us007a2_s1_other_letter_case_of_public_is_refused_on_case_insensitive_filesystems(self) -> None:
        feed = os.path.join(self.tmp, "feed")
        shutil.copytree(os.path.join(FEED, "scripts"), os.path.join(feed, "scripts"), ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(feed, "public", "v1", "seasons"))
        if not os.path.isdir(os.path.join(feed, "PUBLIC")):
            self.skipTest("el sistema de ficheros distingue mayusculas: PUBLIC no es public")
        self.write_inputs([match_row()])
        before = self.snapshot(feed)
        for target in ("PUBLIC/v1/seasons/2026-27.json", "Public/v1/nuevo.json"):
            with self.subTest(target):
                result = run_script([os.path.join(feed, "scripts", "convert-csv"), "--teams", self.teams, "--matches", self.matches,
                                     "--season-id", "2026-27", "--competition-name", "x", "--updated-at", UPDATED_AT,
                                     "--out", os.path.join(feed, target)])
                self.assertEqual(result.returncode, 2, output_of(result))
                self.assertIn("public/", result.stderr)
        self.assertEqual(self.snapshot(feed), before)

    def test_us007a2_c7_a_symlink_into_public_is_refused_too(self) -> None:
        feed = os.path.join(self.tmp, "feed")
        shutil.copytree(os.path.join(FEED, "scripts"), os.path.join(feed, "scripts"), ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(feed, "public"))
        link = os.path.join(self.out_dir, "enlace")
        os.symlink(os.path.join(feed, "public"), link)
        self.write_inputs([match_row()])
        result = run_script([os.path.join(feed, "scripts", "convert-csv"), "--teams", self.teams, "--matches", self.matches,
                             "--season-id", "2026-27", "--competition-name", "x", "--updated-at", UPDATED_AT,
                             "--out", os.path.join(link, "2026-27.json")])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(os.listdir(os.path.join(feed, "public")), [])

    def test_us007a2_c7_converting_the_examples_leaves_the_real_public_directory_unchanged(self) -> None:
        public = os.path.join(FEED, "public")
        before = self.snapshot(public)
        result = self.convert(teams=os.path.join(EXAMPLES, "equipos.csv"), matches=os.path.join(EXAMPLES, "partidos.csv"))
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertEqual(self.snapshot(public), before)

    def test_us007a2_c7_the_examples_are_not_in_public(self) -> None:
        self.assertFalse(os.path.exists(os.path.join(FEED, "public", "equipos.csv")))
        self.assertTrue(os.path.isfile(os.path.join(EXAMPLES, "equipos.csv")))


if __name__ == "__main__":
    unittest.main()
