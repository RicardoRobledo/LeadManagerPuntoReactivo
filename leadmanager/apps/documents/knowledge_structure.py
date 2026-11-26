"""
Canonical structure of the knowledge base.

Every entry is a section (stored as TopicModel) that groups a fixed
set of specific documents (stored as DocumentModel). This module is
the single source of truth: the data migration seeds the database
from here and the knowledge view renders the interface from here.

Both the section and each document carry their own description, and
that split is deliberate. A section can be switched off, and so can a
single document inside it, so no description may enumerate what its
neighbours contain: a section that listed its documents would keep
promising one that was turned off. The section says what the area is
about; each document says what it holds and when to open it.

To add a section or a document, edit this list and create a new data
migration that calls ``sync_knowledge_structure``.
"""

__author__ = 'Ricardo'
__version__ = '0.2'


KNOWLEDGE_STRUCTURE = [
    {
        'name': 'Información de la empresa',
        'description': (
            'Identidad de la empresa: quién es, qué representa y cómo quiere '
            'que se hable de ella. Consulta esta sección cuando la pregunta '
            'sea sobre la empresa misma —su propósito, su posicionamiento o '
            'su personalidad— y no sobre un producto, un precio o un trámite.'
        ),
        'accent_bg': 'bg-blue-100',
        'accent_text': 'text-blue-600',
        'icon': (
            'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 '
            '0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 '
            '1v5m-4 0h4'
        ),
        'documents': [
            {
                'name': 'Datos de contacto',
                'description': (
                    'Los medios por los que se puede contactar a la empresa '
                    'y dónde encontrarla: teléfonos, correos, domicilio, '
                    'redes sociales, sitio web y horarios de atención. '
                    'Consúltalo cuando el cliente pida un teléfono, un '
                    'correo, una dirección o pregunte en qué horario lo '
                    'pueden atender.'
                ),
            },
            {
                'name': 'Nosotros',
                'description': (
                    'Historia de la empresa, su origen, su trayectoria y el '
                    'equipo que la forma. Consúltalo cuando el cliente '
                    'pregunte quiénes son, desde cuándo operan, dónde '
                    'nacieron o quién está detrás del negocio.'
                ),
            },
            {
                'name': 'Nuestro diferenciador',
                'description': (
                    'Lo que distingue a la empresa de su competencia y las '
                    'razones concretas para elegirla. Consúltalo cuando el '
                    'cliente compare con otras opciones, pregunte por qué '
                    'debería comprar aquí o haya que sostener el precio '
                    'frente a una alternativa más barata.'
                ),
            },
            {
                'name': 'Casos de éxito',
                'description': (
                    'Proyectos ya entregados contados uno por uno: qué '
                    'necesitaba el cliente, qué se hizo y qué resultado '
                    'obtuvo. Consúltalo cuando el cliente pida ejemplos, '
                    'pregunte si han resuelto algo parecido a lo suyo o '
                    'necesite ver que la solución ya funcionó en otro lado.'
                ),
            },
            {
                'name': 'Quiénes confiaron en nosotros',
                'description': (
                    'Las empresas y organizaciones relevantes que ya son '
                    'clientes, con su nombre y su giro. Consúltalo cuando el '
                    'cliente pida referencias, pregunte con quién han '
                    'trabajado o cuando haga falta respaldar la propuesta '
                    'con nombres que reconozca.'
                ),
            },
            {
                'name': 'Statement de marca',
                'description': (
                    'La declaración corta que resume qué es la marca y qué '
                    'representa, con su redacción oficial. Consúltalo cuando '
                    'necesites presentar la marca en una frase o conservar la '
                    'forma exacta en que la empresa se describe a sí misma.'
                ),
            },
            {
                'name': 'Objetivo de marca',
                'description': (
                    'El propósito que persigue la marca más allá de vender y '
                    'la meta que se ha fijado. Consúltalo cuando el cliente '
                    'pregunte qué busca la empresa o cuál es su misión, o '
                    'cuando haga falta encuadrar la conversación en el '
                    'propósito del negocio.'
                ),
            },
            {
                'name': 'Propuesta de valor',
                'description': (
                    'El beneficio central que la empresa promete y lo que el '
                    'cliente obtiene al comprar. Consúltalo para explicar qué '
                    'gana el cliente, responder una objeción de precio o '
                    'resumir la oferta en términos de beneficio y no de '
                    'características.'
                ),
            },
            {
                'name': 'Límites, ¿qué somos y qué no somos?',
                'description': (
                    'Los límites explícitos de la marca: lo que la empresa '
                    'hace y lo que no hace, y los temas que quedan fuera de '
                    'su alcance. Consúltalo antes de comprometer algo dudoso, '
                    'para saber cuándo decir que no y para reconocer una '
                    'petición que se sale del giro del negocio.'
                ),
            },
        ],
    },
    {
        'name': 'Estrategia Comercial',
        'description': (
            'Cómo y dónde vende la empresa. Consulta esta sección cuando la '
            'pregunta sea sobre el proceso de compra —canales, lugares, '
            'tiempos, atención— y no sobre el producto en sí ni sobre lo que '
            'ocurre después de la venta.'
        ),
        'accent_bg': 'bg-violet-100',
        'accent_text': 'text-violet-600',
        'icon': 'M13 7h8m0 0v8m0-8l-8 8-4-4-6 6',
        'documents': [
            {
                'name': 'Estrategia omnicanal',
                'description': (
                    'Los canales por los que la empresa vende y atiende '
                    '—tienda física, sitio web, redes sociales, mensajería— y '
                    'cómo se coordinan entre sí. Consúltalo cuando el cliente '
                    'pregunte por dónde puede comprar, si lo atendido en un '
                    'canal continúa en otro, o cuando haya que dirigirlo al '
                    'canal correcto.'
                ),
            },
            {
                'name': 'Nuestro compromiso de venta',
                'description': (
                    'Lo que la empresa se compromete a cumplir durante la '
                    'venta: tiempos de respuesta, condiciones de atención y '
                    'el estándar de servicio prometido. Consúltalo para '
                    'responder qué puede esperar el cliente del proceso, en '
                    'cuánto tiempo y bajo qué condiciones.'
                ),
            },
            {
                'name': 'Puntos de venta',
                'description': (
                    'Ubicaciones físicas, horarios y cobertura de entrega o '
                    'servicio. Consúltalo cuando el cliente pregunte dónde '
                    'están, si hay sucursal en su ciudad, a qué hora abren o '
                    'hasta dónde envían.'
                ),
            },
        ],
    },
    {
        'name': 'Productos y servicios',
        'description': (
            'Lo que la empresa vende, con su detalle técnico y comercial. '
            'Consulta esta sección cuando la pregunta sea sobre el producto o '
            'servicio en sí: qué es, de qué está hecho, cómo se usa o qué '
            'variantes existen.'
        ),
        'accent_bg': 'bg-emerald-100',
        'accent_text': 'text-emerald-600',
        'icon': 'M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4',
        'documents': [
            {
                'name': 'Materiales',
                'description': (
                    'Los materiales con que se elaboran las piezas, sus '
                    'propiedades, durabilidad y cuidados. Consúltalo para '
                    'responder de qué está hecho un producto, cómo limpiarlo '
                    'o conservarlo, si resiste determinado uso, o si puede '
                    'causar alergia o reacción.'
                ),
            },
            {
                'name': 'Información de productos y servicios',
                'description': (
                    'Ficha de cada producto o servicio: qué es, para qué '
                    'sirve, medidas, variantes, presentaciones y '
                    'disponibilidad. Consúltalo para describir un producto '
                    'concreto, comparar variantes o confirmar si existe una '
                    'medida, un color o una presentación.'
                ),
            },
        ],
    },
    {
        'name': 'Aspectos Generales y Legales',
        'description': (
            'Marco legal que rige la marca, el sitio y sus contenidos. '
            'Consulta esta sección solo cuando la pregunta sea jurídica o '
            'sobre permisos de uso; no la uses para condiciones comerciales '
            'ni para reclamaciones de posventa.'
        ),
        'accent_bg': 'bg-slate-200',
        'accent_text': 'text-slate-600',
        'icon': (
            'M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1'
            'm-3 1l-3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 '
            '16V5m0 16H9m3 0h3'
        ),
        'documents': [
            {
                'name': 'Propiedad Intelectual',
                'description': (
                    'Titularidad de diseños, nombres, logotipos, imágenes y '
                    'demás material de la empresa, y qué usos están '
                    'permitidos o prohibidos. Consúltalo cuando pidan '
                    'reproducir, revender, imitar o publicar material de la '
                    'marca, o pregunten de quién es un diseño.'
                ),
            },
            {
                'name': 'Derechos de autor',
                'description': (
                    'Los derechos de autor que aplican a los contenidos '
                    'publicados y las condiciones para citarlos o '
                    'reutilizarlos. Consúltalo cuando pidan permiso para '
                    'copiar textos, fotografías o publicaciones, o pregunten '
                    'cómo atribuir un contenido.'
                ),
            },
        ],
    },
    {
        'name': 'Responsabilidades y Garantías',
        'description': (
            'Lo que la empresa responde frente al cliente después de la '
            'compra. Consulta esta sección ante un reclamo, una falla, una '
            'devolución o cualquier duda sobre qué ocurre cuando algo sale '
            'mal.'
        ),
        'accent_bg': 'bg-amber-100',
        'accent_text': 'text-amber-600',
        'icon': (
            'M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 '
            '014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 '
            '3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 '
            '1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 '
            '3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 '
            '01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 '
            '3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z'
        ),
        'documents': [
            {
                'name': 'Responsabilidad',
                'description': (
                    'El alcance y los límites de lo que la empresa responde: '
                    'de qué se hace cargo, de qué no, y en qué casos se '
                    'deslinda. Consúltalo ante un reclamo por daño, pérdida, '
                    'retraso o mal uso, antes de aceptar o rechazar una '
                    'responsabilidad.'
                ),
            },
            {
                'name': 'Garantías',
                'description': (
                    'Qué cubre y qué no cubre la garantía de cada producto, '
                    'su vigencia, requisitos y cómo hacerla válida. '
                    'Consúltalo cuando el cliente reporte una falla, pregunte '
                    'cuánto tiempo está protegido o qué necesita para '
                    'reclamar.'
                ),
            },
            {
                'name': 'Devoluciones',
                'description': (
                    'Plazos, requisitos, causales aceptadas y procedimiento '
                    'para devolver o cambiar una pieza, y cuándo aplica '
                    'reembolso. Consúltalo cuando el cliente quiera devolver, '
                    'cambiar o cancelar una compra, o pregunte hasta cuándo '
                    'puede hacerlo.'
                ),
            },
            {
                'name': 'Política de comisiones',
                'description': (
                    'El esquema de comisiones y compensación del equipo de '
                    'ventas, con porcentajes, condiciones y momento de pago. '
                    'Consúltalo solo ante dudas internas del equipo '
                    'comercial: no es información para el cliente final.'
                ),
            },
        ],
    },
]


def document_names(section):
    """
    Returns the plain names of the documents of a section.

    The entries used to be plain strings and became dictionaries when
    each document got its own description, so both forms are accepted.

    Args:
        section (dict): one entry of ``KNOWLEDGE_STRUCTURE``.

    Returns:
        list[str]: the document names.
    """
    return [
        document if isinstance(document, str) else document['name']
        for document in section['documents']
    ]


def _get_field(model, field_name):
    """
    Returns a model field, or None when the model does not define it.

    Data migrations receive historical models, so a field added by a
    later migration may not exist yet when this runs.
    """
    try:
        return model._meta.get_field(field_name)
    except Exception:
        return None


def _fit(text, field):
    """
    Trims a value so it fits the column behind ``field``.

    The description started out as a CharField(255) and later became a
    TextField. A migration that runs against the historical model must
    not write more than that column accepts.
    """
    max_length = getattr(field, 'max_length', None)

    if max_length and len(text) > max_length:
        return text[:max_length]

    return text


def sync_knowledge_structure(topic_model, document_model):
    """
    Creates the sections and document slots that are missing.

    It is idempotent: content already loaded is never overwritten.
    Descriptions are rewritten on every run, because they are fixed
    catalogue copy and not information the user loads.

    Args:
        topic_model: TopicModel class (or its historical version).
        document_model: DocumentModel class (or its historical version).

    Returns:
        tuple: (topics_created, documents_created)
    """
    topics_created = 0
    documents_created = 0

    topic_description_field = _get_field(topic_model, 'description')
    document_description_field = _get_field(document_model, 'description')

    for section in KNOWLEDGE_STRUCTURE:
        defaults = {}
        description = None

        if topic_description_field is not None:
            description = _fit(section['description'], topic_description_field)
            defaults['description'] = description

        topic, created = topic_model.objects.get_or_create(
            topic_name=section['name'],
            defaults=defaults,
        )

        if created:
            topics_created += 1
        elif description is not None and topic.description != description:
            topic.description = description
            topic.save(update_fields=['description'])

        for entry in section['documents']:
            # Antes cada documento era solo un nombre.
            if isinstance(entry, str):
                name, document_description = entry, None
            else:
                name = entry['name']
                document_description = entry.get('description')

            document_defaults = {'content': ''}

            if (document_description_field is not None
                    and document_description is not None):
                document_description = _fit(
                    document_description,
                    document_description_field,
                )
                document_defaults['description'] = document_description
            else:
                document_description = None

            document, created = document_model.objects.get_or_create(
                topic_id=topic,
                document_name=name,
                defaults=document_defaults,
            )

            if created:
                documents_created += 1
            elif (document_description is not None
                    and document.description != document_description):
                document.description = document_description
                document.save(update_fields=['description'])

    return topics_created, documents_created
