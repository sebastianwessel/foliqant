"""Versioned editorial definitions for imported classification source labels.

These definitions explain source label names; they are not authoritative gold
annotations or a policy for adjudicating individual records. Ambiguous source
labels remain ambiguous, and blind disagreement still quarantines a record.
"""

from __future__ import annotations

from foliqant.decisions import CategoryCatalog, CategoryDefinition

BANKING77_CATALOG_VERSION = "banking77-editorial-v1"

# Exact upstream spellings are mapping keys; CategoryCatalog supplies native IDs.
# Keep both languages explicit: a language tag must never masquerade as translation.
_BANKING77_DEFINITIONS: dict[str, tuple[str, str]] = {
    "card_arrival": (
        "An ordered card has not arrived, or its delivery status is requested. For a general "
        "delivery-time estimate, see card_delivery_estimate.",
        "Eine bestellte Karte ist nicht angekommen, oder ihr Lieferstatus wird erfragt. "
        "Allgemeine Lieferdauer: card_delivery_estimate.",
    ),
    "card_linking": (
        "Link an existing card to the account or app; not ordering or activating a card.",
        "Eine vorhandene Karte mit Konto oder App verknüpfen; keine Bestellung oder Aktivierung.",
    ),
    "exchange_rate": (
        "Ask about currency exchange rates generally; transaction-specific wrong rates have "
        "separate card-payment and withdrawal categories.",
        "Allgemeine Frage zu Wechselkursen; falsche Kurse konkreter Kartenzahlungen oder "
        "Abhebungen haben eigene Kategorien.",
    ),
    "card_payment_wrong_exchange_rate": (
        "An incorrect exchange rate on a card payment; not a separate payment fee or an ATM "
        "withdrawal rate.",
        "Falscher Wechselkurs einer Kartenzahlung; keine separate Zahlungsgebühr und kein Kurs "
        "einer Geldabhebung.",
    ),
    "extra_charge_on_statement": (
        "An unexplained additional charge on a statement. May overlap with a named fee "
        "category; do not infer the cause of the extra charge.",
        "Unerklärliche zusätzliche Belastung im Auszug. Kann sich mit Gebührenkategorien "
        "überschneiden; Ursache nicht erschließen.",
    ),
    "pending_cash_withdrawal": (
        "A cash withdrawal remains pending; not a declined withdrawal or cash received in the "
        "wrong amount.",
        "Eine Geldabhebung bleibt ausstehend; keine abgelehnte Abhebung und kein falsch "
        "ausgezahlter Betrag.",
    ),
    "fiat_currency_support": (
        "Which fiat currencies the account or service supports; not which cards or currencies "
        "can fund a card top-up.",
        "Welche Fiat-Währungen Konto oder Dienst unterstützen; nicht die Karten oder Währungen "
        "für Kartenaufladungen.",
    ),
    "card_delivery_estimate": (
        "Ask how long card delivery normally takes or is expected to take; an already missing "
        "card can also support card_arrival.",
        "Erwartete oder übliche Lieferdauer einer Karte; eine bereits vermisste Karte kann "
        "auch card_arrival stützen.",
    ),
    "automatic_top_up": (
        "Set up, change, stop or understand automatic top-ups; not a one-off manual top-up.",
        "Automatische Aufladungen einrichten, ändern, stoppen oder verstehen; keine einmalige "
        "manuelle Aufladung.",
    ),
    "card_not_working": (
        "A card does not work, without a more specific supported failure. Virtual-card, "
        "contactless, PIN and declined-payment failures have separate categories.",
        "Eine Karte funktioniert ohne belegten spezifischeren Fehler nicht. Virtuelle Karte, "
        "kontaktloses Zahlen, PIN und Ablehnung haben eigene Kategorien.",
    ),
    "exchange_via_app": (
        "How to exchange currencies using the app; not an exchange fee or a disputed rate.",
        "Währungen über die App wechseln; keine Wechselgebühr oder Beanstandung eines Kurses.",
    ),
    "lost_or_stolen_card": (
        "A physical card is lost or stolen; suspected exposed card details without loss "
        "instead concern compromised_card.",
        "Eine physische Karte wurde verloren oder gestohlen; offengelegte Kartendaten ohne "
        "Kartenverlust betreffen compromised_card.",
    ),
    "age_limit": (
        "Age requirements or restrictions for using the service; not country availability or "
        "identity-check procedure.",
        "Altersvoraussetzungen des Dienstes; keine Länderabdeckung oder Anleitung zur "
        "Identitätsprüfung.",
    ),
    "pin_blocked": (
        "A card PIN is blocked or PIN attempts are exhausted; not changing a usable PIN or "
        "forgetting the app passcode.",
        "Karten-PIN gesperrt oder PIN-Versuche aufgebraucht; kein normaler PIN-Wechsel oder "
        "vergessener App-Zugangscode.",
    ),
    "contactless_not_working": (
        "Contactless card payment does not work; not a general card failure without a "
        "contactless symptom.",
        "Kontaktloses Bezahlen mit der Karte funktioniert nicht; kein allgemeiner Kartenfehler "
        "ohne diesen Bezug.",
    ),
    "top_up_by_bank_transfer_charge": (
        "Fees for funding the account by bank transfer; an outgoing transfer fee belongs to "
        "transfer_fee_charged.",
        "Gebühren für Kontoeinzahlungen per Banküberweisung; Gebühren ausgehender "
        "Überweisungen: transfer_fee_charged.",
    ),
    "pending_top_up": (
        "A top-up remains pending; not a failed or reversed top-up.",
        "Eine Aufladung bleibt ausstehend; keine fehlgeschlagene oder rückgängig gemachte "
        "Aufladung.",
    ),
    "cancel_transfer": (
        "Cancel or stop a transfer; not asking only why a transfer failed or is pending.",
        "Eine Überweisung stornieren oder stoppen; nicht nur nach Fehlschlag oder ausstehendem "
        "Status fragen.",
    ),
    "top_up_limits": (
        "Amount or frequency limits on adding money; not fees or disposable-card usage limits.",
        "Betrags- oder Häufigkeitsgrenzen für Aufladungen; keine Gebühren oder Nutzungslimits "
        "einer Einwegkarte.",
    ),
    "wrong_amount_of_cash_received": (
        "An ATM dispensed an incorrect cash amount; not a fee, exchange rate or unrecognized "
        "withdrawal.",
        "Ein Geldautomat hat einen falschen Bargeldbetrag ausgegeben; keine Gebühr, kein "
        "Wechselkurs oder unbekannte Abhebung.",
    ),
    "card_payment_fee_charged": (
        "A fee charged for a card payment; not the payment exchange rate or a duplicated "
        "principal charge.",
        "Gebühr für eine Kartenzahlung; kein Wechselkurs oder doppelt belasteter Zahlungsbetrag.",
    ),
    "transfer_not_received_by_recipient": (
        "A transfer's intended recipient has not received it. A question only about normal "
        "transfer duration supports transfer_timing.",
        "Der Empfänger hat eine Überweisung nicht erhalten. Eine reine Frage zur üblichen "
        "Dauer stützt transfer_timing.",
    ),
    "supported_cards_and_currencies": (
        "Which payment cards and currencies are supported for card top-ups; general account "
        "currencies belong to fiat_currency_support.",
        "Welche Zahlungskarten und Währungen für Kartenaufladungen unterstützt werden; "
        "allgemeine Kontowährungen: fiat_currency_support.",
    ),
    "getting_virtual_card": (
        "Obtain a virtual card; explicitly disposable virtual cards have their own category.",
        "Eine virtuelle Karte erhalten; ausdrücklich einmalig verwendbare virtuelle Karten "
        "haben eine eigene Kategorie.",
    ),
    "card_acceptance": (
        "Where or for which purchases the card is accepted; supported ATMs are covered by "
        "atm_support.",
        "Wo oder für welche Einkäufe die Karte akzeptiert wird; unterstützte Geldautomaten: "
        "atm_support.",
    ),
    "top_up_reverted": (
        "A top-up was reversed or returned after initiation; not a pending or failed top-up "
        "without reversal.",
        "Eine begonnene Aufladung wurde rückgängig gemacht; keine lediglich ausstehende oder "
        "fehlgeschlagene Aufladung.",
    ),
    "balance_not_updated_after_cheque_or_cash_deposit": (
        "The balance has not reflected a cash or cheque deposit; bank-transfer funding has a "
        "separate category.",
        "Bargeld- oder Scheckeinzahlung fehlt im Kontostand; Banküberweisungen haben eine "
        "eigene Kategorie.",
    ),
    "card_payment_not_recognised": (
        "A card payment is not recognized by the account holder; not an unfamiliar direct "
        "debit or ATM withdrawal.",
        "Der Kontoinhaber erkennt eine Kartenzahlung nicht; keine unbekannte Lastschrift oder "
        "Geldabhebung.",
    ),
    "edit_personal_details": (
        "Change account profile or personal details; not completing identity verification.",
        "Profil- oder persönliche Kontodaten ändern; keine Durchführung der Identitätsprüfung.",
    ),
    "why_verify_identity": (
        "Why identity verification is required; not how to perform it or a reported "
        "verification failure.",
        "Warum eine Identitätsprüfung nötig ist; keine Anleitung oder Meldung eines "
        "Prüfungsfehlers.",
    ),
    "unable_to_verify_identity": (
        "Identity verification cannot be completed or has failed; not a general question about "
        "the procedure.",
        "Identitätsprüfung fehlgeschlagen oder nicht durchführbar; keine allgemeine Frage zum "
        "Ablauf.",
    ),
    "get_physical_card": (
        "Obtain a physical card. The label overlaps with order_physical_card; their names "
        "alone establish no reliable exclusive boundary.",
        "Eine physische Karte erhalten. Überschneidet sich mit order_physical_card; die Namen "
        "begründen keine verlässliche exklusive Abgrenzung.",
    ),
    "visa_or_mastercard": (
        "Whether a card uses Visa or Mastercard, or choosing between those networks; not "
        "general merchant acceptance.",
        "Visa oder Mastercard als Kartennetzwerk erfragen oder auswählen; keine allgemeine "
        "Händlerakzeptanz.",
    ),
    "topping_up_by_card": (
        "How to add money by payment card; specific card eligibility, fees, limits or failures "
        "have separate categories.",
        "Geld per Zahlungskarte aufladen; Karteneignung, Gebühren, Grenzen und konkrete Fehler "
        "haben eigene Kategorien.",
    ),
    "disposable_card_limits": (
        "Usage restrictions or limits of disposable virtual cards; not acquiring one or "
        "general account top-up limits.",
        "Nutzungsbeschränkungen von virtuellen Einwegkarten; keine Beschaffung oder "
        "allgemeinen Aufladelimits.",
    ),
    "compromised_card": (
        "Card details or card security may be compromised; physical card loss and an "
        "identified unrecognized payment have separate categories.",
        "Kartendaten oder Kartensicherheit möglicherweise kompromittiert; Kartenverlust und "
        "konkrete unbekannte Zahlungen haben eigene Kategorien.",
    ),
    "atm_support": (
        "Which ATMs support cash withdrawals with the card; not a specific withdrawal failure "
        "or merchant acceptance.",
        "Welche Geldautomaten Abhebungen mit der Karte unterstützen; kein konkreter "
        "Abhebungsfehler oder Händlerakzeptanz.",
    ),
    "direct_debit_payment_not_recognised": (
        "A direct debit is not recognized by the account holder; not a card payment or ATM "
        "withdrawal.",
        "Der Kontoinhaber erkennt eine Lastschrift nicht; keine Kartenzahlung oder Geldabhebung.",
    ),
    "passcode_forgotten": (
        "The app or account access passcode was forgotten; not the card PIN.",
        "App- oder Kontozugangscode vergessen; nicht die Karten-PIN.",
    ),
    "declined_cash_withdrawal": (
        "An ATM cash withdrawal was declined; not a pending withdrawal or an incorrect "
        "dispensed amount.",
        "Geldabhebung am Automaten abgelehnt; keine ausstehende Abhebung oder falscher "
        "Auszahlungsbetrag.",
    ),
    "pending_card_payment": (
        "A card payment remains pending; not a declined, reversed or duplicated payment.",
        "Eine Kartenzahlung bleibt ausstehend; keine abgelehnte, rückgängig gemachte oder "
        "doppelte Zahlung.",
    ),
    "lost_or_stolen_phone": (
        "A phone used for the service is lost or stolen; not loss of a physical payment card.",
        "Das für den Dienst verwendete Telefon wurde verloren oder gestohlen; kein Verlust "
        "einer Zahlungskarte.",
    ),
    "request_refund": (
        "Ask to obtain or request a refund; an already expected refund missing from the "
        "account belongs to Refund_not_showing_up.",
        "Eine Erstattung beantragen oder erhalten wollen; eine bereits erwartete fehlende "
        "Erstattung: Refund_not_showing_up.",
    ),
    "declined_transfer": (
        "A transfer is explicitly declined or rejected. Failed transfers without a clear "
        "rejection belong to failed_transfer; wording may overlap.",
        "Eine Überweisung wird ausdrücklich abgelehnt. Fehlschlag ohne klare Ablehnung: "
        "failed_transfer; Formulierungen können sich überschneiden.",
    ),
    "Refund_not_showing_up": (
        "An expected or issued refund has not appeared in the account; not the initial request "
        "for a refund.",
        "Eine erwartete oder ausgestellte Erstattung fehlt im Konto; kein erstmaliger "
        "Erstattungsantrag.",
    ),
    "declined_card_payment": (
        "A card payment was declined; not merely pending or a contactless-only malfunction "
        "without a declined payment.",
        "Eine Kartenzahlung wurde abgelehnt; nicht nur ausstehend oder ein kontaktloser Fehler "
        "ohne abgelehnte Zahlung.",
    ),
    "pending_transfer": (
        "A transfer remains pending; not a definite decline or failure, or a general duration "
        "question alone.",
        "Eine Überweisung bleibt ausstehend; keine eindeutige Ablehnung, kein Fehlschlag oder "
        "reine Frage zur üblichen Dauer.",
    ),
    "terminate_account": (
        "Close or terminate the account; not merely block a card or cancel one transaction.",
        "Das Konto schließen; nicht nur eine Karte sperren oder eine einzelne Transaktion "
        "stornieren.",
    ),
    "card_swallowed": (
        "An ATM retained the physical card; not a lost card without ATM retention.",
        "Ein Geldautomat hat die Karte eingezogen; kein Kartenverlust ohne Einzug durch den "
        "Automaten.",
    ),
    "transaction_charged_twice": (
        "The same transaction appears charged twice; not a separately identified fee or refund "
        "delay.",
        "Dieselbe Transaktion wurde offenbar doppelt belastet; keine separat benannte Gebühr "
        "oder verzögerte Erstattung.",
    ),
    "verify_source_of_funds": (
        "Verify or document the origin of funds; not identity verification or verification of "
        "a top-up card.",
        "Herkunft von Geldmitteln nachweisen; keine Identitätsprüfung oder Prüfung einer "
        "Aufladekarte.",
    ),
    "transfer_timing": (
        "Expected or normal transfer processing time. If an actual recipient reports "
        "nonreceipt, transfer_not_received_by_recipient may also apply.",
        "Erwartete oder übliche Überweisungsdauer. Meldet ein konkreter Empfänger Nichterhalt, "
        "kann transfer_not_received_by_recipient ebenfalls passen.",
    ),
    "reverted_card_payment?": (
        "A card payment was reversed or reverted; not a requested refund, pending payment or "
        "declined attempt alone.",
        "Eine Kartenzahlung wurde rückgängig gemacht; kein Erstattungsantrag, ausstehende "
        "Zahlung oder bloße Ablehnung.",
    ),
    "change_pin": (
        "Change the card PIN; not unblock a blocked PIN or reset the app passcode.",
        "Karten-PIN ändern; keine gesperrte PIN entsperren oder App-Zugangscode zurücksetzen.",
    ),
    "beneficiary_not_allowed": (
        "A transfer beneficiary is not permitted; not a general transfer failure without a "
        "beneficiary restriction.",
        "Ein Überweisungsempfänger ist nicht zulässig; kein allgemeiner Fehlschlag ohne "
        "Empfängerbeschränkung.",
    ),
    "transfer_fee_charged": (
        "A fee charged for sending a transfer; fees for funding one's own account by bank "
        "transfer have a separate category.",
        "Gebühr für eine ausgehende Überweisung; Gebühren zur eigenen Kontoaufladung per "
        "Überweisung haben eine eigene Kategorie.",
    ),
    "receiving_money": (
        "Receiving money into the account. May overlap with transfer_into_account; the names "
        "alone do not establish an exclusive boundary.",
        "Geld auf dem Konto empfangen. Kann sich mit transfer_into_account überschneiden; die "
        "Namen allein begründen keine exklusive Abgrenzung.",
    ),
    "failed_transfer": (
        "A transfer failed or could not be completed. Explicit rejection supports "
        "declined_transfer; do not infer an unspecified technical cause.",
        "Eine Überweisung schlug fehl. Ausdrückliche Ablehnung stützt declined_transfer; keine "
        "ungenannte technische Ursache erschließen.",
    ),
    "transfer_into_account": (
        "Transfer money into the account. May overlap with receiving_money; the names alone do "
        "not establish an exclusive boundary.",
        "Geld auf das Konto überweisen. Kann sich mit receiving_money überschneiden; die Namen "
        "allein begründen keine exklusive Abgrenzung.",
    ),
    "verify_top_up": (
        "Verification required for a top-up or its funding card; not general identity or "
        "source-of-funds verification.",
        "Prüfung einer Aufladung oder ihrer Zahlungskarte; keine allgemeine Identitäts- oder "
        "Mittelherkunftsprüfung.",
    ),
    "getting_spare_card": (
        "Obtain an additional or spare card; not an unspecified first physical card or a "
        "virtual card.",
        "Eine zusätzliche oder Ersatzkarte als Reserve erhalten; keine unbestimmte erste "
        "physische oder virtuelle Karte.",
    ),
    "top_up_by_cash_or_cheque": (
        "Whether or how to add money by cash or cheque; a completed deposit missing from the "
        "balance has a separate category.",
        "Ob oder wie Bargeld oder Schecks eingezahlt werden können; eine bereits fehlende "
        "Gutschrift hat eine eigene Kategorie.",
    ),
    "order_physical_card": (
        "Order a physical card. The label overlaps with get_physical_card; their names alone "
        "establish no reliable exclusive boundary.",
        "Eine physische Karte bestellen. Überschneidet sich mit get_physical_card; die Namen "
        "begründen keine verlässliche exklusive Abgrenzung.",
    ),
    "virtual_card_not_working": (
        "A virtual card does not work; not acquiring a virtual card or a physical-card-only "
        "failure.",
        "Eine virtuelle Karte funktioniert nicht; keine Beschaffung oder ausschließlich "
        "physischer Kartenfehler.",
    ),
    "wrong_exchange_rate_for_cash_withdrawal": (
        "An incorrect exchange rate on an ATM withdrawal; not a withdrawal fee or card-payment "
        "exchange rate.",
        "Falscher Wechselkurs einer Geldabhebung; keine Abhebungsgebühr oder Wechselkurs einer "
        "Kartenzahlung.",
    ),
    "get_disposable_virtual_card": (
        "Obtain a disposable virtual card; not an unspecified virtual card or limits on an "
        "existing disposable card.",
        "Eine virtuelle Einwegkarte erhalten; keine unbestimmte virtuelle Karte oder "
        "Nutzungslimits einer vorhandenen Einwegkarte.",
    ),
    "top_up_failed": (
        "A top-up failed or was declined; not merely pending or explicitly reversed.",
        "Eine Aufladung ist fehlgeschlagen oder wurde abgelehnt; nicht nur ausstehend oder "
        "ausdrücklich rückgängig gemacht.",
    ),
    "balance_not_updated_after_bank_transfer": (
        "An incoming bank transfer has not updated the account balance; outgoing-recipient "
        "nonreceipt and cash or cheque deposits differ.",
        "Eine eingehende Banküberweisung fehlt im Kontostand; Nichterhalt beim ausgehenden "
        "Empfänger und Bargeld-/Scheckeinzahlungen sind andere Fälle.",
    ),
    "cash_withdrawal_not_recognised": (
        "A cash withdrawal is not recognized by the account holder; not a recognized "
        "withdrawal with a fee or incorrect amount.",
        "Der Kontoinhaber erkennt eine Geldabhebung nicht; keine bekannte Abhebung mit Gebühr "
        "oder falschem Betrag.",
    ),
    "exchange_charge": (
        "A fee for exchanging currencies; not the quoted exchange rate or a separately "
        "identified payment or withdrawal fee.",
        "Gebühr für einen Währungswechsel; kein Wechselkurs oder separat benannte Zahlungs- "
        "oder Abhebungsgebühr.",
    ),
    "top_up_by_card_charge": (
        "A fee charged for adding money by payment card; not fees for bank-transfer top-ups or "
        "card purchases.",
        "Gebühr für eine Aufladung per Zahlungskarte; keine Überweisungsaufladung oder "
        "Kartenzahlung.",
    ),
    "activate_my_card": (
        "Activate an existing card for use; not link it to an account, order it or track delivery.",
        "Eine vorhandene Karte zur Nutzung aktivieren; nicht verknüpfen, bestellen oder ihre "
        "Lieferung verfolgen.",
    ),
    "cash_withdrawal_charge": (
        "Fees charged for withdrawing cash; not an incorrect exchange rate, dispensed amount "
        "or unrecognized withdrawal.",
        "Gebühren für Geldabhebungen; kein falscher Wechselkurs, Auszahlungsbetrag oder "
        "unbekannte Abhebung.",
    ),
    "card_about_to_expire": (
        "A card's approaching expiry or renewal; not a spare-card request without expiry context.",
        "Bevorstehender Kartenablauf oder Verlängerung; keine Reservekarte ohne Ablaufbezug.",
    ),
    "apple_pay_or_google_pay": (
        "Use or set up Apple Pay or Google Pay; not contactless payment by physical card alone.",
        "Apple Pay oder Google Pay nutzen oder einrichten; nicht ausschließlich kontaktlos mit "
        "physischer Karte zahlen.",
    ),
    "verify_my_identity": (
        "How to complete identity verification or which identity documents are needed; reasons "
        "for verification and failed attempts differ.",
        "Identitätsprüfung durchführen oder benötigte Dokumente erfragen; Begründung und "
        "fehlgeschlagene Versuche sind andere Fälle.",
    ),
    "country_support": (
        "Countries where the service or account is available; not card acceptance at a "
        "particular merchant or supported currencies.",
        "Länder, in denen Dienst oder Konto verfügbar sind; keine Händlerakzeptanz oder "
        "unterstützten Währungen.",
    ),
}


def banking77_catalog(language: str) -> CategoryCatalog:
    """Return all 77 editorial definitions, preserving upstream label identities.

    Keys normalize through CategoryCatalog, including upstream punctuation and
    capitalization. Unsupported languages fail closed rather than mislabel prose.
    """

    if language not in {"en", "de"}:
        raise ValueError("unsupported BANKING77 catalog language")
    index = 0 if language == "en" else 1
    return CategoryCatalog(
        categories=[
            CategoryDefinition(id=label, description=descriptions[index])
            for label, descriptions in _BANKING77_DEFINITIONS.items()
        ]
    )


def banking77_source_labels() -> tuple[str, ...]:
    """Return exact source labels before native ID normalization."""

    return tuple(_BANKING77_DEFINITIONS)
