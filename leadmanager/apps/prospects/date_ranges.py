"""
Resuelve el rango de fechas que piden los filtros del panel.

Las tres pantallas -dashboard, conversaciones y estadísticas- compartían
la misma lógica copiada tres veces, con nombres distintos para lo mismo
('today' en una, 'day' en otra). Aquí vive una sola vez.

Todo se calcula en hora de México, no en UTC: un lead que entra a las
8 de la noche del día 31 tiene que contar para el día 31, no para el 1
del mes siguiente.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.utils import timezone
from django.utils.formats import date_format


mx_zi = ZoneInfo('America/Mexico_City')

TODAY = 'today'
WEEK = 'week'
MONTH = 'month'
LAST_MONTH = 'last_month'
YEAR = 'year'
CUSTOM = 'custom'

INTERVALS = (TODAY, WEEK, MONTH, LAST_MONTH, YEAR, CUSTOM)

# 'day' es como llamaba la pantalla de conversaciones a 'today'. Se
# acepta para no romper los enlaces que alguien ya tenga guardados.
ALIASES = {'day': TODAY}

FORMATO_ENTRADA = '%Y-%m-%d'


class DateRange:
    """
    El rango ya resuelto, listo para filtrar y para repintar el formulario.

    ``date_to`` es EXCLUSIVO: apunta al arranque del día siguiente al
    último día elegido, para que ese último día entre completo. Un filtro
    ``created_at__lt=date_to`` incluye todo el 31 de octubre, no solo su
    medianoche.
    """

    def __init__(self, interval, date_from, date_to, label,
                 date_from_input='', date_to_input=''):
        self.interval = interval
        self.date_from = date_from
        self.date_to = date_to
        self.label = label
        self.date_from_input = date_from_input
        self.date_to_input = date_to_input

    @property
    def filters(self):
        """Los kwargs para un ``.filter()`` sobre ``created_at``."""
        return {
            'created_at__gte': self.date_from,
            'created_at__lt': self.date_to,
        }

    @property
    def days(self):
        """Cuántos días abarca, para decidir cómo agrupar una gráfica."""
        return max((self.date_to - self.date_from).days, 1)

    @property
    def previous(self):
        """
        El periodo inmediatamente anterior, de la misma duración.

        Es contra lo que compara la pantalla de estadísticas: si eliges
        del 1 al 31 de octubre, compara contra los 31 días anteriores.
        """
        duracion = self.date_to - self.date_from
        return DateRange(
            interval=self.interval,
            date_from=self.date_from - duracion,
            date_to=self.date_from,
            label=_describir(self.date_from - duracion, self.date_from),
        )


def _inicio_del_dia(momento):
    return momento.replace(hour=0, minute=0, second=0, microsecond=0)


def _describir(date_from, date_to):
    """Texto legible del rango. date_to llega exclusivo, se resta un día."""
    ultimo = date_to - timedelta(days=1)

    if date_from.date() == ultimo.date():
        return date_format(date_from, 'd M Y')

    if date_from.year == ultimo.year:
        return '{inicio} – {fin}'.format(
            inicio=date_format(date_from, 'd M'),
            fin=date_format(ultimo, 'd M Y'),
        )

    return '{inicio} – {fin}'.format(
        inicio=date_format(date_from, 'd M Y'),
        fin=date_format(ultimo, 'd M Y'),
    )


def _leer_fecha(texto):
    """Convierte 'YYYY-MM-DD' en un datetime con hora de México, o None."""
    if not texto:
        return None

    try:
        ingenua = datetime.strptime(texto.strip(), FORMATO_ENTRADA)
    except (ValueError, AttributeError):
        return None

    return ingenua.replace(tzinfo=mx_zi)


def resolve_range(request, default=TODAY):
    """
    Lee los parámetros del request y devuelve el DateRange correspondiente.

    Ante cualquier dato inválido avisa por ``messages`` y cae al periodo
    por omisión, en vez de reventar o de mostrar datos equivocados en
    silencio.
    """
    ahora = timezone.now().astimezone(mx_zi)

    inicio_hoy = _inicio_del_dia(ahora)
    fin_hoy = inicio_hoy + timedelta(days=1)
    inicio_semana = inicio_hoy - timedelta(days=inicio_hoy.weekday())
    inicio_mes = inicio_hoy.replace(day=1)
    inicio_mes_pasado = (inicio_mes - timedelta(days=1)).replace(day=1)
    inicio_anio = inicio_hoy.replace(month=1, day=1)

    interval = (request.GET.get('interval') or default).strip()
    interval = ALIASES.get(interval, interval)

    if interval not in INTERVALS:
        messages.warning(
            request,
            'Invalid period selected. Showing the default period.',
        )
        interval = default

    if interval == CUSTOM:
        desde = _leer_fecha(request.GET.get('date_from'))
        hasta = _leer_fecha(request.GET.get('date_to'))

        if desde is None or hasta is None:
            messages.warning(
                request,
                'A custom period needs both dates. Showing the default period.',
            )
            interval = default
        else:
            if desde > hasta:
                # Se intercambian en vez de rechazar: es evidente lo que
                # quiso el usuario y no hay razón para hacerlo repetir.
                desde, hasta = hasta, desde
                messages.info(
                    request,
                    'The dates were swapped: the start was after the end.',
                )

            # hasta + 1 día: el último día elegido entra completo.
            date_to = _inicio_del_dia(hasta) + timedelta(days=1)
            date_from = _inicio_del_dia(desde)

            return DateRange(
                interval=CUSTOM,
                date_from=date_from,
                date_to=date_to,
                label=_describir(date_from, date_to),
                date_from_input=date_from.strftime(FORMATO_ENTRADA),
                date_to_input=(date_to - timedelta(days=1)
                               ).strftime(FORMATO_ENTRADA),
            )

    rangos = {
        TODAY: (inicio_hoy, fin_hoy),
        WEEK: (inicio_semana, fin_hoy),
        MONTH: (inicio_mes, fin_hoy),
        # El único que no termina hoy: el mes pasado se mira completo y
        # cerrado, que es justo lo que no se podía hacer antes.
        LAST_MONTH: (inicio_mes_pasado, inicio_mes),
        YEAR: (inicio_anio, fin_hoy),
    }

    date_from, date_to = rangos[interval]

    return DateRange(
        interval=interval,
        date_from=date_from,
        date_to=date_to,
        label=_describir(date_from, date_to),
        date_from_input=date_from.strftime(FORMATO_ENTRADA),
        date_to_input=(date_to - timedelta(days=1)).strftime(FORMATO_ENTRADA),
    )


def bucket_labels(rango):
    """
    Decide cómo agrupar una serie para graficarla, según qué tan largo
    sea el rango. Una barra por día está bien para una semana y es
    ilegible para un año.

    Devuelve (modo, lista_de_cortes) donde cada corte es
    (inicio, fin_exclusivo, etiqueta).
    """
    dias = rango.days

    if dias <= 7:
        modo = 'day'
        formato = 'D'          # Día abreviado en el idioma de la interfaz
    elif dias <= 45:
        modo = 'day'
        formato = 'd M'        # Día y mes abreviado
    else:
        modo = 'month'
        formato = 'M y'        # Mes abreviado y año

    cortes = []

    if modo == 'day':
        actual = rango.date_from
        while actual < rango.date_to:
            siguiente = actual + timedelta(days=1)
            cortes.append((actual, siguiente, date_format(actual, formato)))
            actual = siguiente
        return modo, cortes

    actual = rango.date_from.replace(day=1)
    while actual < rango.date_to:
        if actual.month == 12:
            siguiente = actual.replace(year=actual.year + 1, month=1)
        else:
            siguiente = actual.replace(month=actual.month + 1)
        cortes.append((actual, siguiente, date_format(actual, formato)))
        actual = siguiente

    return modo, cortes
