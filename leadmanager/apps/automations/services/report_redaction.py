"""Excluye los datos de contacto de los textos del cierre diario."""

import re


EMAIL_PATTERN = re.compile(r'(?<![\w.+%-])[\w.+%-]+@[\w-]+(?:\.[\w-]+)+', re.IGNORECASE)
PHONE_PATTERN = re.compile(r'(?<!\w)\+?\d(?:[\s().-]*\d){9,14}(?!\w)')


def redact_contact_data(value, contacts=()):
    """Oculta contactos conocidos y correos/teléfonos escritos en texto libre."""
    replacements = {}
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        for key, marker in (
            ('nombre', '[nombre omitido]'), ('correo', '[correo omitido]'),
            ('telefono', '[teléfono omitido]'), ('empresa', '[empresa omitida]'),
        ):
            text = str(contact.get(key) or '').strip()
            if len(text) >= 2:
                replacements[text] = marker
            if key == 'nombre' and text:
                first_name = text.split()[0]
                if len(first_name) >= 3:
                    replacements.setdefault(first_name, marker)

    ordered = sorted(replacements, key=len, reverse=True)
    names_pattern = re.compile(
        r'(?<!\w)(?:' + '|'.join(re.escape(text) for text in ordered) + r')(?!\w)',
        re.IGNORECASE,
    ) if ordered else None
    markers = {text.casefold(): marker for text, marker in replacements.items()}

    known_phones = []
    for contact in contacts:
        if isinstance(contact, dict):
            digits = re.sub(r'\D', '', str(contact.get('telefono') or ''))
            if len(digits) >= 7:
                known_phones.append(re.compile(
                    r'(?<!\d)\+?' + r'[\s().-]*'.join(digits) + r'(?!\d)'
                ))

    def clean(item):
        if isinstance(item, str):
            # Primero direcciones completas, para no romperlas al ocultar un nombre.
            item = EMAIL_PATTERN.sub('[correo omitido]', item)
            for pattern in known_phones:
                item = pattern.sub('[teléfono omitido]', item)
            item = PHONE_PATTERN.sub('[teléfono omitido]', item)
            if names_pattern:
                item = names_pattern.sub(
                    lambda match: markers.get(match.group().casefold(), '[dato omitido]'), item,
                )
            return item
        if isinstance(item, list):
            return [clean(element) for element in item]
        if isinstance(item, dict):
            return {key: clean(element) for key, element in item.items()}
        return item

    return clean(value)
