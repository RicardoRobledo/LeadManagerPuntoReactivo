from django.db import models
from django.core.validators import MaxLengthValidator

from ..base.models import BaseModel


__author__ = 'Ricardo'
__version__ = '0.1'


# Tope de cada campo largo de la base de conocimiento. Los campos de
# texto de PostgreSQL no tienen límite propio, así que el que importa es
# este: lo que se guarda aquí termina viajando dentro del prompt, y lo
# que no cabe ahí no sirve de nada guardarlo.
MAX_TEXT_LENGTH = 57618


class TopicModel(BaseModel):
    """
    This model define a topic

    Attributes:
        topic_name (str): name of the topic
        description (str): description of what the topic covers. It is meant
            to be read by the AI, so it is long-form and its only limit is
            ``MAX_TEXT_LENGTH``.
        is_active (bool): whether the whole section is offered to the AI. A
            company that does not deal with a subject turns it off instead
            of deleting it, so the slot can be brought back later.
        created_at (datetime): creation date
    """

    topic_name = models.CharField(max_length=255, null=False, blank=False)
    description = models.TextField(
        null=False,
        blank=False,
        validators=[MaxLengthValidator(MAX_TEXT_LENGTH)],
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'topic'
        verbose_name_plural = 'topics'

    def __str__(self):
        return self.topic_name

    def __repr__(self):
        return f"TopicModel(id={self.id}, topic_name={self.topic_name}, description={self.description}, created_at={self.created_at})"


class DocumentModel(BaseModel):
    """
    This model define a document

    Attributes:
        document_name (str): name of the document
        content (str): content of the document. It stays empty until the
            document is loaded, which marks the slot as pending, and it
            cannot go over ``MAX_TEXT_LENGTH``.
        description (str): what the document holds and when to open it.
            It is read by the AI and belongs to the document rather than
            to its section, so switching a document off also removes the
            promise that its content is available.
        is_active (bool): whether this document is offered to the AI. It is
            independent from the section flag: a document is only reachable
            when both its own flag and its section's flag are on.
        topic_id (TopicModel): topic of the document
    """

    document_name = models.CharField(max_length=255, null=False, blank=False)
    content = models.TextField(
        null=False,
        blank=True,
        default='',
        validators=[MaxLengthValidator(MAX_TEXT_LENGTH)],
    )
    description = models.TextField(
        null=False,
        blank=True,
        default='',
        validators=[MaxLengthValidator(MAX_TEXT_LENGTH)],
    )
    is_active = models.BooleanField(default=True)
    topic_id = models.ForeignKey(
        TopicModel, null=False, blank=False, on_delete=models.CASCADE)

    class Meta:
        verbose_name = 'document'
        verbose_name_plural = 'documents'

    def __str__(self):
        return self.document_name

    def __repr__(self):
        return f"DocumentModel(id={self.id}, document_name={self.document_name}, topic_id={self.topic_id_id}, created_at={self.created_at})"
