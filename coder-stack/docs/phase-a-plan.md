# Nächste Ausbaustufe: Klassifizierte Eskalation + Run-Journal

> Öffentliche Snapshot-Kopie aus dem unten genannten Quell-Commit; portable
> Pfade verwenden `$HOME`. Keine lokale Installation oder Laufzeitdaten wurden übernommen.
>
> Historischer Entwurfsstand. Die umgesetzten Phasen A und B sind in
> `docs/reliability-and-gates.md` dokumentiert. Insbesondere wurden die hier
> vorgeschlagenen Prompt-Hashes und Regex-Treffer aus Datenschutzgründen nicht
> in das Journal übernommen; Timeouts, Circuit Breaker und Quality Gates kamen
> als verbindliche Sicherheitsgrenzen hinzu.

## Kontext

Der Stack läuft, aber die Routing-Ebene (`$HOME/.local/bin/hermes-coder`, 406 Zeilen, stdlib-only) ist blind und hat einen echten Defekt. Zwei Befunde aus der Code-Analyse:

**Defekt 1 — Quota-Fehler lösen Capability-Eskalation aus.** `execute_chain()` (Z. 340-362) behandelt *jeden* Exit ≠ 0 identisch und klettert `fast→normal→complex→frontier`. Ein erschöpftes Claude-Abo ist aber kein Fähigkeitsproblem. Aktuell verbrennt eine Quota-Wand die Quota der nächsthöheren Lane gleich mit — bis zu 8 Versuche, davon 4 Codex-Versuche ohne `--max-turns`-Deckel (der Flag ist Claude-only). Das ist exakt der "Agent-Storm", den es zu vermeiden gilt. `$HOME/.local/bin/claude-subscription` hat zudem **kein** Quota-Handling (Codex hat über `$HOME/.local/bin/codex` Multi-Account-Failover).

**Defekt 2 — Null Observability.** `hermes-coder` schreibt keine Logs, keine Run-ID, keinen State. Welche Lane/Vendor/Modell welchen Task erledigt hat, wie lange, mit welchem Exit — nirgends festgehalten. "Messbare Zuverlässigkeit" ist damit heute unmöglich; jede weitere Optimierung wäre Blindflug.

**Kritischer technischer Befund:** `$HOME/.local/bin/codex` Z. 88 nimmt bei `exec` immer den Pipe-Zweig `"$REAL_CODEX" "$@" 2>&1 | tee`. Codex-stderr landet also auf **stdout**. Eine reine stderr-Erfassung würde jeden Codex-Quota-Fehler als `capability_failure` fehlklassifizieren — also nichts reparieren. Beide Streams müssen erfasst werden.

**Ziel:** Fehlerklasse-bewusste Eskalation + ein JSONL-Journal als Messgrundlage. Verhalten im Erfolgsfall bleibt bitidentisch.

## Umfang

Eine Datei geändert, eine Doku nachgezogen. Kein neues Dependency, kein neuer Prozess, kein Daemon.

## Schritte

### 1. Konstanten (nach Z. 116 `READ_ONLY_TASKS`)

Regex-Vokabular **verbatim** aus `$HOME/.local/bin/codex` Z. 76 portieren (`[[:cntrl:]]` → `[^\x00-\x1f\x7f]`), damit beide Ebenen dieselbe Sprache sprechen. Drei Muster:

- `QUOTA_PATTERN_STRONG` — Prosa-Meldungen (`usage_limit_reached`, `quota…exhausted`, `invalid_grant`, …)
- `QUOTA_PATTERN_WRAPPER` — die deutsche Meldung des Codex-Wrappers (`Abos sind derzeit ausgesch…`), die das geteilte Vokabular **nicht** abdeckt
- `QUOTA_PATTERN_WEAK` — nacktes `401`/`429`. **Empfehlung: im MVP weglassen** (siehe Risiken)

Exit-Kontrakt: `EXIT_QUOTA = 75`, `EXIT_HARNESS = 70`, `EXIT_ABORT = 130`. Bewusst sysexits statt 3/4 — Capability-Fehler reichen den Child-Exit durch, ein Child mit Exit 3 dürfte nicht als Quota gelesen werden.

Journal: `$HOME/.hermes/logs/hermes-coder.jsonl` (Dir existiert, `drwx------`, keine Namenskollision), überschreibbar per `HERMES_CODER_LOG`.

### 2. `run()` ersetzen (Z. 212-220) — Tee statt Passthrough

`subprocess.run` → `Popen(stdout=PIPE, stderr=PIPE)` mit **zwei** Reader-Threads (ein Loop über beide Pipes = klassischer Deadlock). Jeder Thread schreibt live nach `sys.stdout.buffer`/`sys.stderr.buffer` **und** hängt an einen gedeckelten Chunk-Puffer (64 KiB Tail) an.

Sicher, weil hermes-coder nie eine interaktive TUI fährt: Claude bekommt `--print` (Z. 191), Codex `exec` (Z. 197) — und Codex' echtes stdout ist ohnehin schon Pipe-zu-`tee`. Kein PTY nötig.

Drei Pflichtdetails:
- `KeyboardInterrupt` um `proc.wait()` fangen → `rc = 130`, sonst wird der heute saubere Ctrl-C-Abbruch zum Traceback
- `rc < 0` → `128 + (-rc)` normalisieren (behebt nebenbei den Bestandsbug: signal-getötetes Child → `SystemExit(-2)` → Shell-Exit 254)
- `print("+ " + shlex.join(cmd))` und den `dry_run`-Early-Return oben unverändert lassen

Rückgabe als `RunResult(rc, output, duration_s, launch_failed)`.

### 3. `classify_failure(rc, output, launch_failed)` — nur bei `rc != 0` aufrufen

Erste Übereinstimmung gewinnt:
1. `launch_failed` oder `rc in (126, 127)` → `harness_error`
2. `rc == 130` → `user_abort`
3. `STRONG` oder `WRAPPER` trifft → `quota_or_auth` (+ `matched_text[:120]`)
4. sonst → `capability_failure`

**Nur bei `rc != 0`.** Bei Erfolg kann der Puffer das Failover-Geplapper des Codex-Wrappers enthalten ("Limit bei .codex; wechsle zu .codex-pro") — aus einem geglückten Failover einen Quota-Abbruch zu machen wäre ein Eigentor.

### 4. Journal

`journal_write(record)` — ein einziger `f.write(json.dumps(...) + "\n")` im `"a"`-Modus. O_APPEND + kurze Zeilen = keine Interleaves bei parallelen Runs. Alles in `except Exception`; bei erstem Fehler eine stderr-Warnung + `_journal_disabled = True`, damit ein kaputtes Log niemals einen Run killt. Leeres `HERMES_CODER_LOG` = aus.

Schema pro Versuch (`event: "attempt"`):
`schema, run_id, ts, attempt_idx, total_attempts, lane, engine, model, effort, task, workdir, starting_lane, escalated_from, duration_s, exit_code, failure_class, matched_text, prompt_chars, prompt_sha256`

Plus eine `event: "run_end"`-Zeile pro Run (auch bei Erfolg/Frühabbruch): `final_exit_code, final_failure_class, attempts_run, total_attempts, duration_s, starting_lane, task, primary`.

**Kein Prompt-Text** — Secret-Risiko und sprengt die Append-Atomarität. `prompt_chars` + 8-Hex-SHA256-Präfix reichen zur Korrelation von Reruns. `matched_text` ist das, was späteres Regex-Tuning empirisch statt geraten macht.

### 5. `execute_chain()` umbauen (Z. 340-362)

Die statische Attempt-Liste aus `build_attempts()` (Z. 146-152) **bleibt unverändert** — dort liegt der chirurgische Gewinn. Neu nur die Reaktion pro Klasse:

- `harness_error` → sofort `EXIT_HARNESS`, Kette **nicht** verbrauchen (heute frisst ein fehlendes Binary still 4 Slots)
- `user_abort` → sofort `EXIT_ABORT`
- `quota_or_auth` → Engine in `blocked_engines`; wenn der **nächste** Eintrag dieselbe Lane + andere Engine ist → `continue` (Cross-Vendor), sonst `EXIT_QUOTA`. **Keine Lane-Eskalation.**
- `capability_failure` → heutiges Verhalten (klettern), aber Versuche mit Engine in `blocked_engines` überspringen

Der Peek `attempts[idx+1][0] == lane and [1] != engine` *ist* exakt "anderer Vendor, gleiche Lane, dann Stopp" — weil `build_attempts` `(lane, primary), (lane, fallback)` bereits benachbart emittiert. Null neuer Chain-Building-Code.

`blocked_engines` schließt den Mischfall: Claude-Quota bei `fast`, Codex-Capability-Fail bei `fast`, Kette klettert zu `normal` — ohne die Sperre würde Claude dort erneut gegen dasselbe erschöpfte Konto laufen.

Eigene Meldung für Quota, damit im Terminal nicht "Escalating" steht, während das Gegenteil passiert.

### 6. `main()` (Z. 365-402)

`run_id` nach dem Arg-Parsing erzeugen, plus `lane`/`primary` an `execute_chain` durchreichen. Bei `--dry-run` Journal-Pfad + `run_id` nach stderr ausgeben, aber **nichts** schreiben — das Journal bleibt ein Protokoll echter Ausgaben.

### 7. `hybrid-coding-orchestration/SKILL.md` nachziehen

Abschnitt 4 (~Z. 54) sagt Hermes heute "Exit ≠ 0 → klettern". Ohne Update ist der Fix für den einzigen Aufrufer unsichtbar. Neue Tabelle:

| Exit | Bedeutung | Hermes-Aktion |
|---|---|---|
| 0 | Erfolg | Verifikation |
| 2 | Aufruffehler | Invocation reparieren |
| 70 | Harness (Binary fehlt) | Stopp, Umgebung fixen, **kein** Retry |
| 75 | Quota/Auth auf beiden Vendors | Stopp, an User melden, **kein** Retry in andere Lane |
| 130 | User-Abbruch | still stoppen |
| sonst | Capability, Kette erschöpft | bestehende Diagnose-Leiter |

## Kritische Dateien

- `$HOME/.local/bin/hermes-coder` — alle Codeänderungen
- `$HOME/.local/bin/codex` — Z. 73-78, Regex-Quelle (nur lesen)
- `$HOME/.hermes/skills/software-development/hybrid-coding-orchestration/SKILL.md` — Exit-Kontrakt
- `$HOME/.hermes/logs/hermes-coder.jsonl` — neu, zur Laufzeit

## Verifikation — ohne echte Quota zu verbrennen

`HERMES_CODER_CLAUDE` / `HERMES_CODER_CODEX` (Z. 68-71) existieren bereits. Stub-Binaries geben Vollabdeckung. **Beide** Vars immer explizit setzen (`CODEX_CLI` fällt sonst auf `shutil.which` zurück).

| # | Stub | Aufruf | Erwartung |
|---|---|---|---|
| 1 | `exit 0` | `--lane fast` | Exit 0, 1 attempt + 1 run_end, Terminal-Output bitidentisch zu heute |
| 2 | `echo "usage limit reached"; exit 1` (stdout) | `--lane normal` | **Exit 75**, genau 2 Versuche, Lane erreicht `complex` nie |
| 3 | dito, aber `>&2` | dito | identisch zu #2 — beweist, dass der Tee beide Streams sieht |
| 4 | Claude=Quota, Codex=`TypeError; exit 1` | `--lane fast` | Codex klettert bis `frontier`, Claude wird auf **jeder** Lane übersprungen |
| 5 | beide `TypeError; exit 1` | `--lane fast` | volle 8er-Kette, Exit 1 — Regressionstest |
| 6 | `HERMES_CODER_CLAUDE=/nope` | `--lane fast` | **Exit 70** nach 1 Versuch, Kette unverbraucht |
| 7 | `kill -INT $$` | beliebig | **Exit 130**, kein Traceback |
| 8 | 5 MB Output, dann Quota-Zeile | beliebig | kein Deadlock, Puffer bei 64 KiB gedeckelt, klassifiziert korrekt |
| 9 | `HERMES_CODER_LOG=/proc/nope/x` + Quota-Stub | beliebig | eine Warnung, Run läuft normal durch, Exit 75 |
| 10 | `--dry-run` | beliebig | Kette unverändert + run_id/Pfad-Zeile, **keine** Journaldatei |

Danach ein echter Smoke-Test: ein trivialer `--task implement --lane fast` in einem Wegwerf-Worktree, `git diff` prüfen, Journalzeile prüfen.

## Abnahmekriterien

1. Erfolgsfall (Exit 0 im ersten Versuch) ist im Terminal **nicht unterscheidbar** von heute.
2. Quota-Fehler eskaliert **nie** eine Lane hoch — nachgewiesen durch Test #2/#3.
3. Fehlendes Binary → Exit 70 nach genau einem Versuch, nicht nach vier.
4. Ctrl-C → Exit 130, kein Traceback.
5. Jeder Versuch erzeugt genau eine JSONL-Zeile; jeder Run genau eine `run_end`-Zeile.
6. Journal-Schreibfehler bricht **keinen** Run ab.
7. Capability-Eskalation verhält sich unverändert (Test #5).
8. `--dry-run` schreibt nichts.

Nach ~2 Wochen Journal beantwortbar (und heute nicht): Wie oft eskaliert wirklich? Welche Lane ist der beste Default? Wie oft ist "Fehler" in Wahrheit Quota? Lohnt Fable? — Das ist die Datengrundlage für die *nächste* Stufe (Quality Gate).

## Risiken

**`(401|429)`-Zweig ist die schärfste Kante.** Im Codex-Wrapper kostet ein Fehltreffer nur einen Account-Wechsel. Hier bricht er eine legitime Eskalation ab — eine sichtbare Regression auf einem heute funktionierenden Pfad. Jeder Output mit `429`/`401` als freistehender Zahl (HTTP-Beispiele, Testzähler, Zeilennummern, Diff-Hunk-Header) löst ihn aus. **Empfehlung: im MVP weglassen.** Der Strong-Zweig deckt jede Prosa-Meldung ab, die beide CLIs tatsächlich ausgeben; `matched_text` im Journal zeigt nach zwei Wochen, ob etwas fehlt.

**stdout wird zur Pipe.** Heute unkritisch (`--print`/`exec` sind per Kontrakt nicht-interaktiv). Sollte die Claude-CLI je einen TTY-gegateten Prompt auf diesem Pfad einführen, hängt der Run stumm statt zu scheitern. Kein Timeout vorgesehen — nur als Failure-Mode kennen.

**`blocked_engines` ist prozesslokal.** Zwei parallele hermes-coder-Runs verbrennen unabhängig voneinander dieselbe erschöpfte Quota. Fix bräuchte geteilten State (Stempeldatei wie `~/.codex-active-home`). Bewusst außerhalb des MVP — das Journal wird zeigen, wie oft es passiert.

**Capture-Reihenfolge:** getrennte Pipes → der Puffer interleavt auf 4-KiB-Chunk-Granularität, nicht bytegenau. Für Regex egal; das Journal ist kein originalgetreues Transkript.
