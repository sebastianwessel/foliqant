"""Exact German prose mappings for authored research and adequacy cases."""

RESEARCH_TRANSLATIONS: dict[str, str] = {
    "Please refund the duplicate service fee. The transaction reference is absent, and the "
    "charged amount is unavailable.": (
        "Bitte erstatten Sie die doppelt berechnete Servicegebühr. Die Transaktionsreferenz "
        "fehlt, und der belastete Betrag ist nicht verfügbar."
    ),
    "Please send the quarterly statement. No statement period was supplied, and the delivery "
    "channel is not stated.": (
        "Bitte senden Sie mir den Quartalsauszug. Es wurde kein Auszugszeitraum angegeben, "
        "und der Zustellweg ist nicht genannt."
    ),
    "Please update my postal address. The new address is missing, and identity-check results "
    "are unavailable.": (
        "Bitte aktualisieren Sie meine Postanschrift. Die neue Anschrift fehlt, und die "
        "Ergebnisse der Identitätsprüfung sind nicht verfügbar."
    ),
    "Please cancel the transfer. The transfer identifier is absent, and the settlement status "
    "is unavailable.": (
        "Bitte stornieren Sie die Überweisung. Die Überweisungskennung fehlt, und der "
        "Abwicklungsstatus ist nicht verfügbar."
    ),
    "Choose the one category matching the explicit requested action.": (
        "Wählen Sie die eine Kategorie aus, die zur ausdrücklich gewünschten Handlung passt."
    ),
    "Use the explicit requested action, independently of missing details.": (
        "Verwenden Sie die ausdrücklich gewünschte Handlung unabhängig von fehlenden Details."
    ),
    "Refund a fee": "Eine Gebühr erstatten",
    "Send a statement": "Einen Auszug senden",
    "Change a postal address": "Eine Postanschrift ändern",
    "Cancel a transfer": "Eine Überweisung stornieren",
    "Is the transaction reference present?": "Ist die Transaktionsreferenz vorhanden?",
    "Is the statement period present?": "Ist der Auszugszeitraum vorhanden?",
    "Is the new postal address present?": "Ist die neue Postanschrift vorhanden?",
    "Is the transfer identifier present?": "Ist die Überweisungskennung vorhanden?",
    "True requires an explicit reference; false requires an explicit statement that the "
    "reference is absent; otherwise answer unknown.": (
        "Wahr erfordert eine ausdrückliche Referenz; falsch erfordert die ausdrückliche "
        "Angabe, dass die Referenz fehlt; antworten Sie andernfalls mit unbekannt."
    ),
    "Was the charged amount above EUR 50?": "Lag der belastete Betrag über EUR 50?",
    "Was electronic delivery requested?": "Wurde eine elektronische Zustellung verlangt?",
    "Did the identity check pass?": "Wurde die Identitätsprüfung bestanden?",
    "Was the transfer already settled?": "War die Überweisung bereits abgewickelt?",
    "True or false requires the fact itself; an unavailable fact is unknown.": (
        "Wahr oder falsch erfordert die Tatsache selbst; eine nicht verfügbare Tatsache ist "
        "unbekannt."
    ),
    "The explicit requested action matches the selected category.": (
        "Die ausdrücklich gewünschte Handlung entspricht der ausgewählten Kategorie."
    ),
    "The source explicitly states that the transaction reference is absent.": (
        "Die Quelle gibt ausdrücklich an, dass die Transaktionsreferenz fehlt."
    ),
    "The source explicitly states that the statement period is absent.": (
        "Die Quelle gibt ausdrücklich an, dass der Auszugszeitraum fehlt."
    ),
    "The source explicitly states that the new postal address is absent.": (
        "Die Quelle gibt ausdrücklich an, dass die neue Postanschrift fehlt."
    ),
    "The source explicitly states that the transfer identifier is absent.": (
        "Die Quelle gibt ausdrücklich an, dass die Überweisungskennung fehlt."
    ),
    "The allowed source says the required fact is unavailable.": (
        "Die zulässige Quelle gibt an, dass die erforderliche Tatsache nicht verfügbar ist."
    ),
    "The charged amount.": "Der belastete Betrag.",
    "The delivery channel.": "Der Zustellweg.",
    "The identity-check result.": "Das Ergebnis der Identitätsprüfung.",
    "The settlement status.": "Der Abwicklungsstatus.",
    "Policy version 1 sets the filing deadline to June 30, 2026.": (
        "Richtlinienversion 1 legt die Einreichungsfrist auf den 30. Juni 2026 fest."
    ),
    "Policy version 2, which supersedes version 1, sets the filing deadline to July 31, 2026.": (
        "Richtlinienversion 2, die Version 1 ersetzt, legt die Einreichungsfrist auf den "
        "31. Juli 2026 fest."
    ),
    "Does the controlling policy change the filing deadline to July 31, 2026?": (
        "Ändert die maßgebliche Richtlinie die Einreichungsfrist auf den 31. Juli 2026?"
    ),
    "Policy version 1 sets the appeal deadline to March 10, 2027.": (
        "Richtlinienversion 1 legt die Einspruchsfrist auf den 10. März 2027 fest."
    ),
    "Policy version 2, which supersedes version 1, sets the appeal deadline to April 5, 2027.": (
        "Richtlinienversion 2, die Version 1 ersetzt, legt die Einspruchsfrist auf den "
        "5. April 2027 fest."
    ),
    "Does the controlling policy change the appeal deadline to April 5, 2027?": (
        "Ändert die maßgebliche Richtlinie die Einspruchsfrist auf den 5. April 2027?"
    ),
    "Policy version 1 sets the payment deadline to September 1, 2026.": (
        "Richtlinienversion 1 legt die Zahlungsfrist auf den 1. September 2026 fest."
    ),
    "Policy version 2, which supersedes version 1, sets the payment deadline to September 15, "
    "2026.": (
        "Richtlinienversion 2, die Version 1 ersetzt, legt die Zahlungsfrist auf den "
        "15. September 2026 fest."
    ),
    "Does the controlling policy change the payment deadline to September 15, 2026?": (
        "Ändert die maßgebliche Richtlinie die Zahlungsfrist auf den 15. September 2026?"
    ),
    "Policy version 1 sets the response deadline to November 20, 2026.": (
        "Richtlinienversion 1 legt die Antwortfrist auf den 20. November 2026 fest."
    ),
    "Policy version 2, which supersedes version 1, sets the response deadline to December 2, "
    "2026.": (
        "Richtlinienversion 2, die Version 1 ersetzt, legt die Antwortfrist auf den "
        "2. Dezember 2026 fest."
    ),
    "Does the controlling policy change the response deadline to December 2, 2026?": (
        "Ändert die maßgebliche Richtlinie die Antwortfrist auf den 2. Dezember 2026?"
    ),
    "The explicitly superseding policy version controls over the earlier version.": (
        "Die ausdrücklich ersetzende Richtlinienversion ist gegenüber der früheren Version "
        "maßgeblich."
    ),
    "The later policy explicitly supersedes the earlier deadline.": (
        "Die spätere Richtlinie ersetzt ausdrücklich die frühere Frist."
    ),
    "The 2025 report states expense 20 and revenue 120.": (
        "Der Bericht 2025 nennt einen Aufwand von 20 und einen Umsatz von 120."
    ),
    "For the 2025 report, is expense divided by revenue below 20 percent?": (
        "Liegt im Bericht 2025 der Aufwand geteilt durch den Umsatz unter 20 Prozent?"
    ),
    "Divide expense by revenue and compare the result with 20 percent.": (
        "Teilen Sie den Aufwand durch den Umsatz und vergleichen Sie das Ergebnis mit 20 Prozent."
    ),
    "The expense-to-revenue ratio is 20/120, which is below 20 percent.": (
        "Das Verhältnis von Aufwand zu Umsatz beträgt 20/120 und liegt damit unter 20 Prozent."
    ),
    "The report states expense 20; revenue and the reporting period are absent.": (
        "Der Bericht nennt einen Aufwand von 20; Umsatz und Berichtszeitraum fehlen."
    ),
    "Revenue denominator.": "Umsatz als Nenner.",
    "Reporting period.": "Berichtszeitraum.",
    "At year-end 2025, debt is 45 and total assets are 100.": (
        "Zum Jahresende 2025 betragen die Schulden 45 und die Gesamtaktiva 100."
    ),
    "At year-end 2025, is debt divided by total assets below 50 percent?": (
        "Liegen zum Jahresende 2025 die Schulden geteilt durch die Gesamtaktiva unter 50 Prozent?"
    ),
    "Divide debt by total assets and compare the result with 50 percent.": (
        "Teilen Sie die Schulden durch die Gesamtaktiva und vergleichen Sie das Ergebnis mit "
        "50 Prozent."
    ),
    "The debt-to-total-assets ratio is 45/100, which is below 50 percent.": (
        "Das Verhältnis von Schulden zu Gesamtaktiva beträgt 45/100 und liegt damit unter "
        "50 Prozent."
    ),
    "Debt is 45; total assets and the measurement date are absent.": (
        "Die Schulden betragen 45; Gesamtaktiva und Bewertungsstichtag fehlen."
    ),
    "Total-assets denominator.": "Gesamtaktiva als Nenner.",
    "Measurement date.": "Bewertungsstichtag.",
    "For 2025, the management fee is 1.5 and average AUM is 1000.": (
        "Für 2025 beträgt die Verwaltungsgebühr 1.5 und das durchschnittlich verwaltete "
        "Vermögen 1000."
    ),
    "For 2025, is the management fee divided by average AUM below 0.2 percent?": (
        "Liegt für 2025 die Verwaltungsgebühr geteilt durch das durchschnittlich verwaltete "
        "Vermögen unter 0.2 Prozent?"
    ),
    "Divide the management fee by average AUM and compare with 0.2 percent.": (
        "Teilen Sie die Verwaltungsgebühr durch das durchschnittlich verwaltete Vermögen und "
        "vergleichen Sie das Ergebnis mit 0.2 Prozent."
    ),
    "The management-fee-to-average-AUM ratio is 1.5/1000, or 0.15 percent, which is below 0.2 "
    "percent.": (
        "Das Verhältnis der Verwaltungsgebühr zum durchschnittlich verwalteten Vermögen beträgt "
        "1.5/1000 beziehungsweise 0.15 Prozent und liegt damit unter 0.2 Prozent."
    ),
    "The management fee is 1.5; average AUM and the period are absent.": (
        "Die Verwaltungsgebühr beträgt 1.5; das durchschnittlich verwaltete Vermögen und der "
        "Zeitraum fehlen."
    ),
    "Average-AUM denominator.": "Durchschnittlich verwaltetes Vermögen als Nenner.",
    "At March 31, 2026, current assets are 150 and current liabilities are 100.": (
        "Zum 31. März 2026 betragen die kurzfristigen Vermögenswerte 150 und die kurzfristigen "
        "Verbindlichkeiten 100."
    ),
    "At March 31, 2026, are current assets divided by current liabilities at least 1.25?": (
        "Betragen zum 31. März 2026 die kurzfristigen Vermögenswerte geteilt durch die "
        "kurzfristigen Verbindlichkeiten mindestens 1.25?"
    ),
    "Divide current assets by current liabilities and compare with 1.25.": (
        "Teilen Sie die kurzfristigen Vermögenswerte durch die kurzfristigen Verbindlichkeiten "
        "und vergleichen Sie das Ergebnis mit 1.25."
    ),
    "The current ratio is 150/100, or 1.5, which is at least 1.25.": (
        "Der Liquiditätsgrad beträgt 150/100 beziehungsweise 1.5 und damit mindestens 1.25."
    ),
    "Current assets are 150; current liabilities and the measurement date are absent.": (
        "Die kurzfristigen Vermögenswerte betragen 150; die kurzfristigen Verbindlichkeiten und "
        "der Bewertungsstichtag fehlen."
    ),
    "Current-liabilities denominator.": "Kurzfristige Verbindlichkeiten als Nenner.",
    "Both ratio inputs and the stated period are required.": (
        "Beide Eingabewerte des Verhältnisses und der angegebene Zeitraum sind erforderlich."
    ),
    "The ratio cannot be computed for the required period.": (
        "Das Verhältnis kann für den erforderlichen Zeitraum nicht berechnet werden."
    ),
    "Internal policy P applies from January 1, 2027 to firms in Country A.": (
        "Die interne Richtlinie P gilt ab dem 1. Januar 2027 für Unternehmen in Land A."
    ),
    "The case file does not identify whether the subject is a firm and omits its jurisdiction "
    "and event date.": (
        "Aus der Fallakte geht nicht hervor, ob es sich bei der betroffenen Einheit um ein "
        "Unternehmen handelt; außerdem fehlen Rechtsraum und Ereignisdatum."
    ),
    "Internal policy Q applies to fixed-rate products issued after July 1, 2026.": (
        "Die interne Richtlinie Q gilt für Festzinsprodukte, die nach dem 1. Juli 2026 "
        "ausgegeben wurden."
    ),
    "The product was issued on August 4, 2026, but its rate type is not recorded.": (
        "Das Produkt wurde am 4. August 2026 ausgegeben, seine Zinsart ist jedoch nicht erfasst."
    ),
    "Internal policy R applies only to professional customers in Region B.": (
        "Die interne Richtlinie R gilt nur für professionelle Kunden in Region B."
    ),
    "The customer is in Region B, but the customer's classification is absent.": (
        "Der Kunde befindet sich in Region B, aber seine Einstufung fehlt."
    ),
    "Internal policy S applies to online transfers submitted from October 1, 2026.": (
        "Die interne Richtlinie S gilt für Onlineüberweisungen, die ab dem 1. Oktober 2026 "
        "eingereicht werden."
    ),
    "The transfer was submitted on October 3, 2026, but its channel is unavailable.": (
        "Die Überweisung wurde am 3. Oktober 2026 eingereicht, ihr Übertragungskanal ist jedoch "
        "nicht verfügbar."
    ),
    "Does the internal policy apply to this case?": (
        "Gilt die interne Richtlinie für diesen Fall?"
    ),
    "Every applicability condition in the policy must be resolved from the case.": (
        "Jede Anwendbarkeitsbedingung der Richtlinie muss anhand des Falls geklärt werden."
    ),
    "At least one required applicability fact is absent from the case.": (
        "Mindestens eine für die Anwendbarkeit erforderliche Tatsache fehlt im Fall."
    ),
    "Whether the subject is a firm.": (
        "Ob es sich bei der betroffenen Einheit um ein Unternehmen handelt."
    ),
    "Case jurisdiction.": "Rechtsraum des Falls.",
    "Case event date.": "Ereignisdatum des Falls.",
    "Product rate type.": "Zinsart des Produkts.",
    "Customer classification.": "Einstufung des Kunden.",
    "Transfer channel.": "Übertragungskanal der Überweisung.",
    "The report lists 18 branch offices.": "Der Bericht nennt 18 Zweigstellen.",
    "Was the required capital threshold satisfied?": (
        "Wurde die erforderliche Kapitalschwelle eingehalten?"
    ),
    "Required capital threshold and measured capital.": (
        "Erforderliche Kapitalschwelle und gemessenes Kapital."
    ),
    "The company renewed its office lease for five years.": (
        "Das Unternehmen verlängerte seinen Büromietvertrag um fünf Jahre."
    ),
    "Was the required liquidity buffer maintained?": (
        "Wurde der erforderliche Liquiditätspuffer eingehalten?"
    ),
    "Required liquidity threshold and measured liquid assets.": (
        "Erforderliche Liquiditätsschwelle und gemessene liquide Mittel."
    ),
    "The fund employs 42 people.": "Der Fonds beschäftigt 42 Personen.",
    "Was the management-fee cap respected?": (
        "Wurde die Obergrenze für die Verwaltungsgebühr eingehalten?"
    ),
    "Applicable fee cap and charged management fee.": (
        "Anwendbare Gebührenobergrenze und berechnete Verwaltungsgebühr."
    ),
    "The payments team spent EUR 30,000 on marketing.": (
        "Das Zahlungsteam gab EUR 30,000 für Marketing aus."
    ),
    "Was the transfer settled within the required time?": (
        "Wurde die Überweisung innerhalb der vorgeschriebenen Frist abgewickelt?"
    ),
    "Required settlement deadline and actual settlement time.": (
        "Vorgeschriebene Abwicklungsfrist und tatsächlicher Abwicklungszeitpunkt."
    ),
    "Use only facts that measure both the stated requirement and the actual result.": (
        "Verwenden Sie nur Tatsachen, die sowohl die genannte Anforderung als auch das "
        "tatsächliche Ergebnis messen."
    ),
    "The supplied source does not address the required comparison.": (
        "Die bereitgestellte Quelle behandelt den erforderlichen Vergleich nicht."
    ),
}


ADEQUACY_TRANSLATIONS: dict[str, str] = {
    "Identify every requested action; do not perform the actions. For a fee-refund request also "
    "report its transaction reference, or unknown with the missing fact if no reference is "
    "supplied. Other execution details are not required. Do not add unrequested actions.": (
        "Ermitteln Sie jede gewünschte Handlung; führen Sie die Handlungen nicht aus. Geben Sie "
        "bei einer Gebührenerstattung außerdem die Transaktionsreferenz an oder unbekannt samt "
        "fehlender Tatsache, wenn keine Referenz vorliegt. Weitere Ausführungsdetails sind nicht "
        "erforderlich. Fügen Sie keine nicht gewünschten Handlungen hinzu."
    ),
    "Please send my statement and update my postal address.": (
        "Bitte senden Sie mir meinen Auszug und aktualisieren Sie meine Postanschrift."
    ),
    "Requested actions: send the statement; update the postal address.": (
        "Gewünschte Handlungen: den Auszug senden; die Postanschrift aktualisieren."
    ),
    "Both requested actions are listed. The task requires identification, not execution details, "
    "and the answer adds no other action.": (
        "Beide gewünschten Handlungen sind aufgeführt. Die Aufgabe verlangt ihre Ermittlung, "
        "keine Ausführungsdetails, und die Antwort fügt keine weitere Handlung hinzu."
    ),
    "Requested actions: send the statement.": "Gewünschte Handlungen: den Auszug senden.",
    "The answer omits the explicitly requested postal-address update, so it fails the requirement "
    "to identify every requested action.": (
        "Die Antwort lässt die ausdrücklich gewünschte Aktualisierung der Postanschrift aus und "
        "erfüllt daher nicht die Anforderung, jede gewünschte Handlung zu ermitteln."
    ),
    "Please send my statement.": "Bitte senden Sie mir meinen Auszug.",
    "Requested actions: refund a fee. Evidence: Please send my statement.": (
        "Gewünschte Handlungen: eine Gebühr erstatten. Beleg: Bitte senden Sie mir meinen Auszug."
    ),
    "The statement request does not support a fee refund. The answer both adds an unrequested "
    "action and omits the actual request.": (
        "Die Anforderung eines Auszugs stützt keine Gebührenerstattung. Die Antwort fügt eine "
        "nicht gewünschte Handlung hinzu und lässt zugleich die tatsächliche Anforderung aus."
    ),
    "Please refund the duplicate fee; the transaction reference is missing.": (
        "Bitte erstatten Sie die doppelt berechnete Gebühr; die Transaktionsreferenz fehlt."
    ),
    "Requested actions: refund the duplicate fee. Transaction reference: unknown; missing fact: "
    "the transaction reference.": (
        "Gewünschte Handlungen: die doppelt berechnete Gebühr erstatten. Transaktionsreferenz: "
        "unbekannt; fehlende Tatsache: die Transaktionsreferenz."
    ),
    "The refund action is preserved, and the absent transaction reference is reported as unknown "
    "with its missing fact, as the task requires.": (
        "Die Erstattung bleibt erhalten, und die fehlende Transaktionsreferenz wird wie von der "
        "Aufgabe verlangt als unbekannt samt fehlender Tatsache ausgewiesen."
    ),
    "Extract revenue and expense from the supplied report in EUR. Return both named fields. Copy "
    "a stated value exactly; if a field is not supplied, return unknown for that field and name "
    "the missing fact. Do not substitute one field for another.": (
        "Entnehmen Sie dem bereitgestellten Bericht Umsatz und Aufwand in EUR. Geben Sie beide "
        "benannten Felder zurück. Übernehmen Sie einen angegebenen Wert exakt; wenn ein Feld nicht "
        "angegeben ist, geben Sie dafür unbekannt zurück und nennen Sie die fehlende Tatsache. "
        "Ersetzen Sie kein Feld durch ein anderes."
    ),
    "The report states revenue of EUR 120 and expense of EUR 20.": (
        "Der Bericht nennt einen Umsatz von EUR 120 und einen Aufwand von EUR 20."
    ),
    "Revenue: EUR 120. Expense: EUR 20.": "Umsatz: EUR 120. Aufwand: EUR 20.",
    "Both required report fields are present with their explicitly stated values and currency.": (
        "Beide erforderlichen Berichtsfelder liegen mit ihren ausdrücklich genannten Werten und "
        "der Währung vor."
    ),
    "Revenue: EUR 120.": "Umsatz: EUR 120.",
    "The answer omits expense even though the task requires both fields and the report supplies "
    "the expense value.": (
        "Die Antwort lässt den Aufwand aus, obwohl die Aufgabe beide Felder verlangt und der "
        "Bericht den Aufwandswert liefert."
    ),
    "Revenue: EUR 120. Expense: EUR 120; evidence: revenue of EUR 120.": (
        "Umsatz: EUR 120. Aufwand: EUR 120; Beleg: Umsatz von EUR 120."
    ),
    "Revenue is incorrectly used as the expense value. The report explicitly gives expense as "
    "EUR 20, so the proposed expense is unsupported.": (
        "Der Umsatz wird fälschlich als Aufwandswert verwendet. Der Bericht nennt ausdrücklich "
        "einen Aufwand von EUR 20; der vorgeschlagene Aufwand ist daher nicht belegt."
    ),
    "The report states revenue of EUR 120. It does not supply an expense value.": (
        "Der Bericht nennt einen Umsatz von EUR 120. Einen Aufwandswert enthält er nicht."
    ),
    "Revenue: EUR 120. Expense: unknown; missing fact: the expense value.": (
        "Umsatz: EUR 120. Aufwand: unbekannt; fehlende Tatsache: der Aufwandswert."
    ),
    "The answer preserves the known revenue and reports the absent expense as unknown with the "
    "required missing fact.": (
        "Die Antwort erhält den bekannten Umsatz und weist den fehlenden Aufwand als unbekannt "
        "samt der erforderlichen fehlenden Tatsache aus."
    ),
    "Assess the supplied fictional rule using only the stated country and filing date. Return the "
    "rule identifier and whether it applies: true when all stated conditions hold, false when a "
    "condition is explicitly contradicted, otherwise unknown with the missing applicability fact. "
    "Do not infer an unstated country or date.": (
        "Beurteilen Sie die bereitgestellte fiktive Regel ausschließlich anhand des genannten "
        "Landes und Einreichungsdatums. Geben Sie die Regelkennung und ihre Anwendbarkeit zurück: "
        "wahr, wenn alle genannten Bedingungen erfüllt sind, falsch, wenn eine Bedingung "
        "ausdrücklich widerlegt ist, andernfalls unbekannt samt der für die Anwendbarkeit "
        "fehlenden "
        "Tatsache. Leiten Sie kein nicht genanntes Land oder Datum ab."
    ),
    "Rule R1 applies only to reports filed in Country A on or after 2027-01-01. This report was "
    "filed in Country A on 2027-02-01.": (
        "Regel R1 gilt nur für Berichte, die am oder nach dem 2027-01-01 in Land A eingereicht "
        "wurden. Dieser Bericht wurde am 2027-02-01 in Land A eingereicht."
    ),
    "Rule identifier: R1. Applies: true.": "Regelkennung: R1. Gilt: wahr.",
    "The answer names R1 and correctly finds both conditions satisfied: Country A and a filing "
    "date after the rule's start date.": (
        "Die Antwort nennt R1 und stellt zutreffend fest, dass beide Bedingungen erfüllt sind: "
        "Land A und ein Einreichungsdatum nach dem Beginn der Regel."
    ),
    "Applies: true.": "Gilt: wahr.",
    "The applicability result is supported, but the required rule identifier is missing, so the "
    "answer is incomplete.": (
        "Das Ergebnis zur Anwendbarkeit ist belegt, aber die erforderliche Regelkennung fehlt; "
        "daher ist die Antwort unvollständig."
    ),
    "Rule R1 applies only to reports filed in Country A on or after 2027-01-01. This report was "
    "filed in Country B on 2027-02-01.": (
        "Regel R1 gilt nur für Berichte, die am oder nach dem 2027-01-01 in Land A eingereicht "
        "wurden. Dieser Bericht wurde am 2027-02-01 in Land B eingereicht."
    ),
    "Rule identifier: R1. Applies: true; the report was filed in Country A.": (
        "Regelkennung: R1. Gilt: wahr; der Bericht wurde in Land A eingereicht."
    ),
    "The answer invents Country A despite the explicit Country B filing. The country condition "
    "fails, so true is not supported.": (
        "Die Antwort erfindet Land A trotz der ausdrücklichen Einreichung in Land B. Die "
        "Länderbedingung ist nicht erfüllt; wahr ist daher nicht belegt."
    ),
    "Rule R1 applies only to reports filed in Country A on or after 2027-01-01. This report was "
    "filed on 2027-02-01; its filing country is not supplied.": (
        "Regel R1 gilt nur für Berichte, die am oder nach dem 2027-01-01 in Land A eingereicht "
        "wurden. Dieser Bericht wurde am 2027-02-01 eingereicht; sein Einreichungsland ist nicht "
        "angegeben."
    ),
    "Rule identifier: R1. Applies: unknown; missing fact: the filing country.": (
        "Regelkennung: R1. Gilt: unbekannt; fehlende Tatsache: das Einreichungsland."
    ),
    "The answer names the rule and correctly leaves applicability unknown because the country "
    "condition cannot be checked from the supplied facts.": (
        "Die Antwort nennt die Regel und lässt die Anwendbarkeit zutreffend unbekannt, weil die "
        "Länderbedingung anhand der bereitgestellten Tatsachen nicht geprüft werden kann."
    ),
    "Calculate each plan's annual total as twelve monthly charges plus its one-time setup charge. "
    "Return the annual total for both Plan A and Plan B in EUR, and identify the cheaper plan or a "
    "tie. Any total with a missing component is unknown; if a total is unknown, the comparison is "
    "also unknown. Name each missing component.": (
        "Berechnen Sie die Jahressumme jedes Tarifs als zwölf Monatsgebühren zuzüglich der "
        "einmaligen Einrichtungsgebühr. Geben Sie die Jahressummen für Tarif A und Tarif B in EUR "
        "zurück und bestimmen Sie den günstigeren Tarif oder einen Gleichstand. Jede Summe mit "
        "einer fehlenden Komponente ist unbekannt; ist eine Summe unbekannt, ist auch der "
        "Vergleich "
        "unbekannt. Nennen Sie jede fehlende Komponente."
    ),
    "Plan A costs EUR 20 per month plus a EUR 10 setup charge. Plan B costs EUR 15 per month plus "
    "a EUR 5 setup charge.": (
        "Tarif A kostet EUR 20 pro Monat zuzüglich einer Einrichtungsgebühr von EUR 10. Tarif B "
        "kostet EUR 15 pro Monat zuzüglich einer Einrichtungsgebühr von EUR 5."
    ),
    "Plan A annual total: EUR 250. Plan B annual total: EUR 185. Cheaper: Plan B.": (
        "Jahressumme Tarif A: EUR 250. Jahressumme Tarif B: EUR 185. Günstiger: Tarif B."
    ),
    "The totals follow the supplied formula: 12 times 20 plus 10 is 250, and 12 times 15 plus 5 is "
    "185. Both totals and the cheaper plan are correct.": (
        "Die Summen folgen der vorgegebenen Formel: 12 mal 20 plus 10 ergibt 250, und 12 mal 15 "
        "plus 5 ergibt 185. Beide Summen und der günstigere Tarif sind korrekt."
    ),
    "Plan A annual total: EUR 250. Cheaper: Plan B.": (
        "Jahressumme Tarif A: EUR 250. Günstiger: Tarif B."
    ),
    "The answer omits Plan B's required annual total, although its components are available. A "
    "correct comparison alone does not satisfy the full task.": (
        "Die Antwort lässt die erforderliche Jahressumme von Tarif B aus, obwohl ihre Komponenten "
        "vorliegen. Ein korrekter Vergleich allein erfüllt nicht die gesamte Aufgabe."
    ),
    "Plan A annual total: EUR 30. Plan B annual total: EUR 20. Cheaper: Plan B.": (
        "Jahressumme Tarif A: EUR 30. Jahressumme Tarif B: EUR 20. Günstiger: Tarif B."
    ),
    "The proposed totals use only one monthly charge plus setup. They do not follow the required "
    "twelve-month formula, despite the correct cheaper plan.": (
        "Die vorgeschlagenen Summen verwenden nur eine Monatsgebühr zuzüglich Einrichtung. Sie "
        "folgen trotz des korrekt bestimmten günstigeren Tarifs nicht der erforderlichen "
        "Zwölfmonatsformel."
    ),
    "Plan A costs EUR 20 per month plus a EUR 10 setup charge. Plan B has a EUR 5 setup charge; "
    "its monthly charge is not supplied.": (
        "Tarif A kostet EUR 20 pro Monat zuzüglich einer Einrichtungsgebühr von EUR 10. Tarif B "
        "hat eine Einrichtungsgebühr von EUR 5; seine Monatsgebühr ist nicht angegeben."
    ),
    "Plan A annual total: EUR 250. Plan B annual total: unknown; missing fact: Plan B's monthly "
    "charge. Cheaper: unknown because Plan B's total is unknown.": (
        "Jahressumme Tarif A: EUR 250. Jahressumme Tarif B: unbekannt; fehlende Tatsache: die "
        "Monatsgebühr von Tarif B. Günstiger: unbekannt, weil die Summe von Tarif B unbekannt ist."
    ),
    "The known Plan A total is calculated correctly. The answer preserves both required unknowns "
    "and names the missing Plan B monthly charge.": (
        "Die bekannte Summe von Tarif A ist korrekt berechnet. Die Antwort erhält beide "
        "erforderlichen unbekannten Angaben und nennt die fehlende Monatsgebühr von Tarif B."
    ),
    "Does the proposed answer satisfy every requirement of the supplied task contract?": (
        "Erfüllt die vorgeschlagene Antwort jede Anforderung des bereitgestellten Aufgabenvertrags?"
    ),
    "Check the proposed answer's correctness, completeness, supporting evidence, and compliance "
    "with the supplied task's required outputs and unknown behavior.": (
        "Prüfen Sie die vorgeschlagene Antwort auf Korrektheit, Vollständigkeit, stützende Belege "
        "und Einhaltung der verlangten Ausgaben sowie des Umgangs mit unbekannten Angaben."
    ),
    "Return true only if all requirements hold, false if a supplied fact establishes a failure, "
    "and unknown only if a required assessment fact is absent.": (
        "Geben Sie nur dann wahr zurück, wenn alle Anforderungen erfüllt sind, falsch, wenn eine "
        "bereitgestellte Tatsache einen Fehler belegt, und unbekannt nur dann, wenn eine für die "
        "Beurteilung erforderliche Tatsache fehlt."
    ),
    "The proposed answer is the object being assessed, not independent evidence that its "
    "assertions are true. Do not invent an execution or legal-contract requirement.": (
        "Die vorgeschlagene Antwort ist der zu beurteilende Gegenstand und kein unabhängiger Beleg "
        "für die Richtigkeit ihrer Aussagen. Erfinden Sie keine Ausführungs- oder rechtliche "
        "Vertragsanforderung."
    ),
}
