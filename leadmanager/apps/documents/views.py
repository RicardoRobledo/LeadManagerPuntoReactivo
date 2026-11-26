import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from .models import MAX_TEXT_LENGTH, TopicModel, DocumentModel
from .pdf import PdfError, extract_text, validate_pdf


__author__ = 'Ricardo'
__version__ = '0.1'


PAGE_SIZE = 20


def parse_json_body(request):
    """
    Reads the request body as JSON.

    Returns:
        tuple: (data, error_response). Only one of them is not None.
    """
    if not request.body:
        return {}, None

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return None, JsonResponse(
            {'error': 'La solicitud no tiene un formato JSON válido'},
            status=400,
        )

    if not isinstance(data, dict):
        return None, JsonResponse(
            {'error': 'La solicitud debe contener un objeto JSON'},
            status=400,
        )

    return data, None


TRUE_VALUES = ('true', '1', 'yes', 'on')
FALSE_VALUES = ('false', '0', 'no', 'off')


def parse_bool(value):
    """
    Normalizes a boolean coming from JSON or from the query string.

    Returns:
        bool or None. None means the value is not a recognizable boolean.
    """
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        lowered = value.strip().lower()

        if lowered in TRUE_VALUES:
            return True

        if lowered in FALSE_VALUES:
            return False

    return None


def length_error(field, value):
    """
    Rejects a text that goes over the limit.

    Returns:
        JsonResponse or None. None means the length is acceptable. La
        respuesta trae el largo recibido y el permitido, para que quien
        la lea sepa cuánto sobra y no solo que no cupo.
    """
    if len(value) <= MAX_TEXT_LENGTH:
        return None

    field_label = {
        'content': 'El contenido',
        'description': 'La descripción',
    }.get(field, field)

    return JsonResponse(
        {
            'error': (
                f'{field_label} tiene {len(value)} caracteres y el máximo '
                f'son {MAX_TEXT_LENGTH}.'
            ),
            'field': field,
            'length': len(value),
            'max_length': MAX_TEXT_LENGTH,
            'length_is_valid': False,
        },
        status=400,
    )


def length_report(value):
    """
    Says whether a stored text fits within the limit.

    Se reporta en cada respuesta porque un registro guardado antes de
    que existiera el tope puede excederlo, y eso no se ve mirando el
    texto.
    """
    return {
        'length': len(value),
        'max_length': MAX_TEXT_LENGTH,
        'length_is_valid': len(value) <= MAX_TEXT_LENGTH,
    }


def topic_to_dict(topic):
    """Serializes a TopicModel instance."""
    return {
        'id': topic.id,
        'topic_name': topic.topic_name,
        'description': topic.description,
        'description_length': length_report(topic.description),
        'is_active': topic.is_active,
        'created_at': topic.created_at,
        'updated_at': topic.updated_at,
    }


def document_to_dict(document):
    """Serializes a DocumentModel instance."""
    return {
        'id': document.id,
        'document_name': document.document_name,
        'content': document.content,
        'content_length': length_report(document.content),
        'description': document.description,
        'description_length': length_report(document.description),
        # Un documento sembrado nace vacío: sigue pendiente de carga.
        'is_loaded': bool(document.content),
        'is_active': document.is_active,
        'topic_id': document.topic_id_id,
        'topic_name': document.topic_id.topic_name,
        # Un documento activo dentro de una sección apagada tampoco se
        # entrega: quien consume el catálogo necesita ver ambas banderas.
        'topic_is_active': document.topic_id.is_active,
        'created_at': document.created_at,
        'updated_at': document.updated_at,
    }


# ==========================================
# TOPICS
# ==========================================

class TopicListCreateView(LoginRequiredMixin, View):
    """Lists topics and creates new ones."""

    def get(self, request):
        queryset = TopicModel.objects.all().order_by('topic_name')

        search = request.GET.get('search', '').strip()

        if search:
            queryset = queryset.filter(topic_name__icontains=search)

        is_active = parse_bool(request.GET.get('is_active'))

        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)

        paginator = Paginator(queryset, PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get('page', 1))

        return JsonResponse(
            {
                'topics': [topic_to_dict(t) for t in page_obj],
                'page': page_obj.number,
                'num_pages': paginator.num_pages,
                'count': paginator.count,
            },
            status=200,
        )

    def post(self, request):
        data, error = parse_json_body(request)

        if error:
            return error

        topic_name = (data.get('topic_name') or '').strip()

        if not topic_name:
            return JsonResponse(
                {'error': 'El nombre del apartado es obligatorio'},
                status=400,
            )

        if len(topic_name) > 255:
            return JsonResponse(
                {'error': 'El nombre del apartado debe tener como máximo 255 caracteres'},
                status=400,
            )

        description = (data.get('description') or '').strip()

        error = length_error('description', description)

        if error:
            return error

        # Una sección nace incluida; solo se apaga a propósito.
        is_active = parse_bool(data.get('is_active'))

        topic = TopicModel.objects.create(
            topic_name=topic_name,
            description=description,
            is_active=True if is_active is None else is_active,
        )

        return JsonResponse(
            {'topic': topic_to_dict(topic)},
            status=201,
        )


class TopicDetailView(LoginRequiredMixin, View):
    """Retrieves, updates and deletes a single topic."""

    def get(self, request, topic_id):
        topic = get_object_or_404(TopicModel, pk=topic_id)

        return JsonResponse(
            {'topic': topic_to_dict(topic)},
            status=200,
        )

    def put(self, request, topic_id):
        topic = get_object_or_404(TopicModel, pk=topic_id)

        data, error = parse_json_body(request)

        if error:
            return error

        if 'topic_name' in data:
            topic_name = (data.get('topic_name') or '').strip()

            if not topic_name:
                return JsonResponse(
                    {'error': 'El nombre del apartado no puede estar vacío'},
                    status=400,
                )

            if len(topic_name) > 255:
                return JsonResponse(
                    {'error': 'El nombre del apartado debe tener como máximo 255 caracteres'},
                    status=400,
                )

            topic.topic_name = topic_name

        if 'description' in data:
            description = (data.get('description') or '').strip()

            error = length_error('description', description)

            if error:
                return error

            topic.description = description

        if 'is_active' in data:
            is_active = parse_bool(data.get('is_active'))

            if is_active is None:
                return JsonResponse(
                    {'error': 'El estado de inclusión debe ser verdadero o falso'},
                    status=400,
                )

            topic.is_active = is_active

        topic.save()

        return JsonResponse(
            {'topic': topic_to_dict(topic)},
            status=200,
        )

    # PATCH y PUT comparten la misma lógica: solo se
    # actualizan los campos presentes en el cuerpo.
    patch = put

    def delete(self, request, topic_id):
        topic = get_object_or_404(TopicModel, pk=topic_id)

        # Los documentos asociados se eliminan en cascada.
        documents_deleted = topic.documentmodel_set.count()
        topic.delete()

        return JsonResponse(
            {
                'deleted': True,
                'documents_deleted': documents_deleted,
            },
            status=200,
        )


# ==========================================
# DOCUMENTS
# ==========================================

class DocumentListCreateView(LoginRequiredMixin, View):
    """Lists documents and creates new ones."""

    def get(self, request):
        queryset = (
            DocumentModel.objects
            .select_related('topic_id')
            .order_by('-created_at')
        )

        topic_id = request.GET.get('topic_id')

        if topic_id:
            queryset = queryset.filter(topic_id=topic_id)

        search = request.GET.get('search', '').strip()

        if search:
            queryset = queryset.filter(document_name__icontains=search)

        is_active = parse_bool(request.GET.get('is_active'))

        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)

        # Filtra por el resultado de ambas banderas, que es lo que de
        # verdad determina si el documento se entrega o no.
        available = parse_bool(request.GET.get('available'))

        if available is not None:
            if available:
                queryset = queryset.filter(
                    is_active=True,
                    topic_id__is_active=True,
                )
            else:
                queryset = queryset.exclude(
                    is_active=True,
                    topic_id__is_active=True,
                )

        paginator = Paginator(queryset, PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get('page', 1))

        return JsonResponse(
            {
                'documents': [document_to_dict(d) for d in page_obj],
                'page': page_obj.number,
                'num_pages': paginator.num_pages,
                'count': paginator.count,
            },
            status=200,
        )

    def post(self, request):
        data, error = parse_json_body(request)

        if error:
            return error

        document_name = (data.get('document_name') or '').strip()
        content = (data.get('content') or '').strip()
        description = (data.get('description') or '').strip()
        topic_id = data.get('topic_id')

        for field, value in (('content', content),
                             ('description', description)):
            error = length_error(field, value)

            if error:
                return error

        if not document_name:
            return JsonResponse(
                {'error': 'El nombre del documento es obligatorio'},
                status=400,
            )

        if len(document_name) > 255:
            return JsonResponse(
                {'error': 'El nombre del documento debe tener como máximo 255 caracteres'},
                status=400,
            )

        if not topic_id:
            return JsonResponse(
                {'error': 'Debes seleccionar un apartado'},
                status=400,
            )

        topic = TopicModel.objects.filter(pk=topic_id).first()

        if not topic:
            return JsonResponse(
                {'error': 'El apartado no existe'},
                status=404,
            )

        # Un documento nace incluido; solo se apaga a propósito.
        is_active = parse_bool(data.get('is_active'))

        document = DocumentModel.objects.create(
            document_name=document_name,
            content=content,
            description=description,
            is_active=True if is_active is None else is_active,
            topic_id=topic,
        )

        return JsonResponse(
            {'document': document_to_dict(document)},
            status=201,
        )


class DocumentDetailView(LoginRequiredMixin, View):
    """Retrieves, updates and deletes a single document."""

    def get(self, request, document_id):
        document = get_object_or_404(
            DocumentModel.objects.select_related('topic_id'),
            pk=document_id,
        )

        return JsonResponse(
            {'document': document_to_dict(document)},
            status=200,
        )

    def put(self, request, document_id):
        document = get_object_or_404(
            DocumentModel.objects.select_related('topic_id'),
            pk=document_id,
        )

        data, error = parse_json_body(request)

        if error:
            return error

        if 'document_name' in data:
            document_name = (data.get('document_name') or '').strip()

            if not document_name:
                return JsonResponse(
                    {'error': 'El nombre del documento no puede estar vacío'},
                    status=400,
                )

            if len(document_name) > 255:
                return JsonResponse(
                    {'error': 'El nombre del documento debe tener como máximo 255 caracteres'},
                    status=400,
                )

            document.document_name = document_name

        if 'content' in data:
            # Un contenido vacío regresa el documento a estado pendiente.
            content = (data.get('content') or '').strip()

            error = length_error('content', content)

            if error:
                return error

            document.content = content

        if 'description' in data:
            description = (data.get('description') or '').strip()

            error = length_error('description', description)

            if error:
                return error

            document.description = description

        if 'is_active' in data:
            is_active = parse_bool(data.get('is_active'))

            if is_active is None:
                return JsonResponse(
                    {'error': 'El estado de inclusión debe ser verdadero o falso'},
                    status=400,
                )

            document.is_active = is_active

        if 'topic_id' in data:
            topic = TopicModel.objects.filter(pk=data.get('topic_id')).first()

            if not topic:
                return JsonResponse(
                    {'error': 'El apartado no existe'},
                    status=404,
                )

            document.topic_id = topic

        document.save()

        return JsonResponse(
            {'document': document_to_dict(document)},
            status=200,
        )

    patch = put

    def delete(self, request, document_id):
        document = get_object_or_404(DocumentModel, pk=document_id)
        document.delete()

        return JsonResponse(
            {'deleted': True},
            status=200,
        )


class DocumentUploadView(LoginRequiredMixin, View):
    """
    Loads a document from a PDF file.

    El PDF no se guarda: lo que se consulta después es el texto, así
    que el archivo se lee una vez, su texto pasa a ser el contenido del
    documento y el archivo se descarta.
    """

    def post(self, request, document_id):
        document = get_object_or_404(
            DocumentModel.objects.select_related('topic_id'),
            pk=document_id,
        )

        uploaded_file = request.FILES.get('file')

        if not uploaded_file:
            return JsonResponse(
                {'error': 'No se recibió ningún archivo'},
                status=400,
            )

        try:
            validate_pdf(uploaded_file)
            content, pages = extract_text(uploaded_file)
        except PdfError as error:
            return JsonResponse(
                {'error': str(error)},
                status=400,
            )

        # Un PDF escaneado abre sin problema pero no trae texto: si se
        # guardara vacío, el documento quedaría pendiente sin explicar
        # por qué.
        if not content:
            return JsonResponse(
                {
                    'error': (
                        'El PDF no contiene texto seleccionable, '
                        'probablemente es un escaneo. Súbelo en un PDF '
                        'con texto o pega el contenido con «Editar».'
                    )
                },
                status=422,
            )

        # Un PDF puede traer mucho más texto del que cabe. Se revisa
        # aquí, ya extraído, porque el peso del archivo no dice cuántos
        # caracteres trae dentro.
        error = length_error('content', content)

        if error:
            return error

        document.content = content
        document.save()

        return JsonResponse(
            {
                'document': document_to_dict(document),
                'pages': pages,
                'characters': len(content),
            },
            status=200,
        )
