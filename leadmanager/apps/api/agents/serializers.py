import re

from django.core.validators import RegexValidator
from rest_framework import serializers


phone_validator = RegexValidator(
    regex=r'^\+?\d{8,20}$',
    message="El teléfono debe contener solo dígitos (8 a 20), con '+' opcional al inicio."
)


def utm_field():
    """
    Un UTM tal como lo manda el sitio.

    Opcional en los dos sentidos: el sitio dice que siempre los incluye,
    pero vacíos cuando no hay dato, y además un cliente que todavía no
    actualice su tag debe seguir funcionando. Vacío significa "no se sabe
    de dónde llegó", no es un error.

    El tope coincide con el de la columna en la base: así un valor
    demasiado largo se rechaza aquí con un mensaje claro, en vez de
    reventar al guardar.
    """
    return serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        default='',
    )


class ChatbotFormSerializer(serializers.Serializer):
    name = serializers.CharField(
        min_length=2,
        max_length=150,
        trim_whitespace=True,
    )
    email = serializers.EmailField(
        max_length=254,
    )
    phone = serializers.CharField(
        validators=[phone_validator],
    )
    subject = serializers.CharField(
        default='Quiero más información',
        trim_whitespace=True,
    )
    message = serializers.CharField(
        min_length=10,
        max_length=2000,
        trim_whitespace=True,
    )

    utm_source = utm_field()
    utm_medium = utm_field()
    utm_campaign = utm_field()
    utm_content = utm_field()
    utm_term = utm_field()
    traffic_origin = utm_field()

    def validate_name(self, value):
        # Solo letras (incluye acentos/ñ) y espacios; rechaza nombres que
        # son puros números o contienen símbolos raros de spam/inyección.
        if not re.match(r'^[A-Za-zÀ-ÿ\s]+$', value):
            raise serializers.ValidationError(
                "El nombre solo puede contener letras y espacios."
            )
        return value.strip()

    def validate_phone(self, value):
        # Normaliza quitando espacios, guiones o paréntesis antes de que
        # el RegexValidator del campo evalúe el formato final.
        cleaned = re.sub(r'[\s\-\(\)]', '', value)
        return cleaned

    def validate_message(self, value):
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError(
                "El mensaje no puede estar vacío o contener solo espacios."
            )
        return stripped


class WebChatSerializer(serializers.Serializer):
    message = serializers.CharField(
        min_length=1,
        max_length=2000,
        trim_whitespace=True,
    )
    session_id = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    name = serializers.CharField(
        max_length=150,
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    email = serializers.EmailField(
        max_length=200,
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )
    business_unit = serializers.CharField(
        max_length=100,
        required=True,
        allow_blank=True,
        trim_whitespace=True,
    )
    company_name = serializers.CharField(
        max_length=100,
        required=True,
        allow_blank=True,
        trim_whitespace=True,
    )
    # El tope cuenta la cadena tal como llega, con sus espacios y guiones,
    # así que va más arriba de los 20 dígitos que se permiten: sin ese
    # margen, "33 3749 5441 1234 5678" se rechaza antes de poder limpiarlo.
    phone = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    # Medio por el que el prospecto quiere que lo contacten; 'phone' es el
    # WhatsApp del widget. En blanco no hay preferencia y el teléfono sigue
    # siendo opcional.
    #
    # Sin ``trim_whitespace``: es argumento de CharField, no de ChoiceField,
    # y con él el módulo completo deja de importar.
    preferred_contact_method = serializers.ChoiceField(
        choices=['email', 'phone'],
        required=True,
        allow_blank=True,
    )
    utm_source = utm_field()
    utm_medium = utm_field()
    utm_campaign = utm_field()
    utm_content = utm_field()
    utm_term = utm_field()
    traffic_origin = utm_field()

    def validate_email(self, value):
        # Se normaliza a minúsculas porque la unicidad de
        # ``ProspectModel.email`` sí distingue mayúsculas en Postgres,
        # mientras que la búsqueda en Odoo usa ``=ilike`` y no. Sin esto,
        # Foo@x.com y foo@x.com abrirían dos prospectos distintos.
        return value.strip().lower()

    def validate_message(self, value):
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError(
                "El mensaje no puede estar vacío o contener solo espacios."
            )
        return stripped

    def validate_session_id(self, value):
        return value.strip() or None

    def validate_phone(self, value):
        # Opcional: vacío pasa tal cual y el lead se crea sin teléfono.
        cleaned = re.sub(r'[\s\-\(\)]', '', value or '')

        if not cleaned:
            return ''

        # Se valida aquí, y no con ``validators=[phone_validator]`` en el
        # campo, porque DRF corre los validadores del campo ANTES de este
        # método: un número escrito como "(33) 3749-5441" se rechazaría
        # antes de que hubiera oportunidad de limpiarlo.
        #
        # Solo dígitos, sin "+": el teléfono acaba en Odoo, y ahí conviene
        # que todos los registros tengan la misma forma.
        if not re.match(r'^\d{8,20}$', cleaned):
            raise serializers.ValidationError(
                'El teléfono debe contener solo dígitos (10 a 20).'
            )

        return cleaned

    def validate(self, attrs):
        # Corre después de validate_phone: aquí el teléfono ya llega limpio.
        metodo = attrs.get('preferred_contact_method')

        # Por WhatsApp el teléfono es el medio mismo: sin él no hay a dónde
        # escribirle.
        if metodo == 'phone' and not attrs.get('phone'):
            raise serializers.ValidationError({
                'phone': 'El teléfono es obligatorio cuando el medio de contacto es WhatsApp.'
            })

        # Por correo el contacto se queda con el correo que ya dio. Si aun así
        # llega un teléfono, se descarta para que no termine en el lead como
        # si lo hubiera dejado para que le escribieran.
        if metodo == 'email':
            attrs['phone'] = ''

        return attrs
