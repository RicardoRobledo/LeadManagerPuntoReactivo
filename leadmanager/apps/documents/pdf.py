"""
Reading of the PDF files uploaded to the knowledge base.

The file itself is never stored: what the AI consumes is text, so the
document is read once, its text is saved as content and the upload is
discarded. Everything that can go wrong with the file is reported as
``PdfError`` with a message meant to be shown to whoever uploaded it.
"""

import re

from pypdf import PdfReader


__author__ = 'Ricardo'
__version__ = '0.1'


# El archivo se lee entero en memoria para extraerle el texto, así que
# el límite protege al servidor, no al almacenamiento.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

PDF_SIGNATURE = b'%PDF-'


class PdfError(Exception):
    """The uploaded file cannot be accepted or cannot be read."""


def validate_pdf(uploaded_file):
    """
    Checks that the upload really is a PDF.

    La extensión dice qué quiso subir quien lo subió y la firma dice
    qué es de verdad; se revisan las dos. El content-type no se toma en
    cuenta: lo declara el navegador a partir del registro del sistema,
    así que un PDF legítimo puede llegar como "application/octet-stream"
    y rechazarlo por eso sería un falso negativo.

    Raises:
        PdfError: with a message ready to be shown to the user.
    """
    if uploaded_file.size == 0:
        raise PdfError('El archivo está vacío.')

    if uploaded_file.size > MAX_UPLOAD_BYTES:
        megabytes = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise PdfError(f'El archivo supera el límite de {megabytes} MB.')

    name = (uploaded_file.name or '').lower()

    if not name.endswith('.pdf'):
        raise PdfError('Solo se aceptan archivos PDF.')

    header = uploaded_file.read(len(PDF_SIGNATURE))
    uploaded_file.seek(0)

    if header != PDF_SIGNATURE:
        raise PdfError('El archivo no es un PDF válido.')


def page_text(page):
    """
    Text of a single page, with the lines the document really has.

    El modo por omisión de pypdf corta el texto en cada operación de
    dibujo, y un PDF exportado desde un procesador de textos suele
    dibujar palabra por palabra: el resultado es una palabra por
    renglón. El modo ``layout`` agrupa por posición en la página, que
    es como se lee el documento. Si esa lectura falla o no devuelve
    nada, se cae al modo normal, que sale feo pero sale.
    """
    try:
        text = page.extract_text(extraction_mode='layout')
    except Exception:
        text = ''

    return text or page.extract_text() or ''


def clean_text(text):
    """
    Leaves the extracted text readable for the AI.

    Un PDF trae saltos y espacios que vienen de la maquetación, no del
    contenido: sin limpiarlos el texto guardado pesa de más y se lee
    peor.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    joined = '\n'.join(lines)

    # Tres o más saltos seguidos son separación visual, no párrafos.
    return re.sub(r'\n{3,}', '\n\n', joined).strip()


def extract_text(uploaded_file):
    """
    Extracts the text of an already validated PDF.

    Returns:
        tuple: (text, page_count). The text comes back empty when the
            PDF holds no selectable text, which is what a scan looks
            like. El número de páginas se devuelve aquí para no volver
            a abrir el archivo solo para contarlas.

    Raises:
        PdfError: when the file is damaged or protected by a password.
    """
    uploaded_file.seek(0)

    try:
        reader = PdfReader(uploaded_file)

        # Un PDF con contraseña de apertura no se puede leer; uno que
        # solo prohíbe imprimir o copiar se abre con contraseña vacía.
        if reader.is_encrypted and not reader.decrypt(''):
            raise PdfError(
                'El PDF está protegido con contraseña. '
                'Quítale la protección y vuelve a subirlo.'
            )

        pages = [page_text(page) for page in reader.pages]
    except PdfError:
        raise
    except Exception as error:
        # El archivo lo sube quien sea y uno dañado hace que pypdf
        # falle de muchas formas distintas. Atraparlas todas es lo que
        # convierte un archivo malo en un mensaje y no en un error 500.
        raise PdfError(f'No se pudo leer el PDF: {error}')

    return clean_text('\n\n'.join(pages)), len(pages)
