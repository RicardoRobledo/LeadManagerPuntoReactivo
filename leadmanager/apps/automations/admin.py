from django.contrib import admin, messages
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseRedirect
from django.forms.models import BaseInlineFormSet
from django.utils.html import format_html, format_html_join
from .agent_configuration import DEFAULT_BUSINESS_UNIT_NAME, with_prompt_defaults
from .models import AgentBusinessUnitModel, AgentModel, AgentRunModel, AgentStepModel, AgentEventModel, ConversationAnalysisModel, LearningAnnotationModel, PendingRunModel, WeeklyAnalysisBatchModel


class AgentBusinessUnitAdminForm(forms.ModelForm):
    class Meta:
        model = AgentBusinessUnitModel
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.name == DEFAULT_BUSINESS_UNIT_NAME:
            for field in ('name', 'agent', 'description'):
                if field in self.fields:
                    self.fields[field].disabled = True


class AgentAdminForm(forms.ModelForm):
    class Meta:
        model = AgentModel
        fields = '__all__'

    def clean(self):
        values = super().clean()
        if values.get('mode') == AgentModel.ModeEnum.SHADOW_TESTING:
            reason = self.instance.get_shadow_mode_block_reason(
                category=values.get('category'), key=values.get('key'),
            )
            if reason:
                self.add_error('mode', reason)
        return values


class AgentBusinessUnitInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        for form in self.forms:
            if (form.instance.name == DEFAULT_BUSINESS_UNIT_NAME
                    and form.cleaned_data.get('DELETE')):
                raise forms.ValidationError('No se puede quitar la unidad predeterminada NO_DETERMINADO.')


class AgentBusinessUnitInline(admin.TabularInline):
    model = AgentBusinessUnitModel
    form = AgentBusinessUnitAdminForm
    formset = AgentBusinessUnitInlineFormSet
    fields = ('name', 'description')
    extra = 0


@admin.register(AgentModel)
class AgentAdmin(admin.ModelAdmin):
    form = AgentAdminForm
    list_display = ('name', 'key', 'category', 'mode', 'is_active', 'learning_enabled', 'updated_at')
    list_editable = ('learning_enabled',)
    list_filter = ('mode', 'is_active', 'learning_enabled')
    search_fields = ('name', 'key')
    readonly_fields = ('name', 'is_active', 'channels', 'created_at', 'updated_at', 'reminder_schedule_display')
    inlines = (AgentBusinessUnitInline,)

    fieldsets = (
        ('Agente', {'fields': ('key', 'name', 'is_active', 'role', 'description', 'category', 'mode', 'avatar', 'channels')}),
        ('Recordatorios', {'fields': ('reminder_schedule_display',)}),
        ('Aprendizaje', {'fields': ('learning_enabled',)}),
        ('Configuración completa', {'fields': ('prompt',)}),
        ('Fechas', {'fields': ('created_at', 'updated_at')}),
    )

    @admin.display(description='Intervalos configurados')
    def reminder_schedule_display(self, obj):
        schedule = with_prompt_defaults((obj.prompt if obj else {}) or {}).get('reminder_schedule', [])
        labels = (
            ('Inicio', 'Primer recordatorio'),
            ('Primer recordatorio', 'Segundo recordatorio'),
            ('Segundo recordatorio', 'Tercer recordatorio'),
        )
        rows = []
        for index, item in enumerate(schedule[:3]):
            start, end = labels[index]
            unit = 'horas' if item['unit'] == 'hours' else 'días'
            status = 'Habilitado' if item.get('enabled', True) else 'Deshabilitado'
            rows.append((
                format_html('{} → {}', start, end),
                f"{item['amount']} {unit}",
                status,
            ))
        if not rows:
            return 'No hay intervalos configurados.'
        return format_html(
            '<table><thead><tr><th>Etapa</th><th>Espera</th><th>Estado</th></tr></thead><tbody>{}</tbody></table>',
            format_html_join('', '<tr><td>{}</td><td>{}</td><td>{}</td></tr>', rows),
        )


@admin.register(AgentBusinessUnitModel)
class AgentBusinessUnitAdmin(admin.ModelAdmin):
    form = AgentBusinessUnitAdminForm
    list_display = ('name', 'agent', 'description')
    list_filter = ('agent',)
    search_fields = ('name', 'description', 'agent__name')

    def has_change_permission(self, request, obj=None):
        return (obj is None or obj.name != DEFAULT_BUSINESS_UNIT_NAME) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return (obj is None or obj.name != DEFAULT_BUSINESS_UNIT_NAME) and super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset.exclude(name=DEFAULT_BUSINESS_UNIT_NAME))


@admin.register(LearningAnnotationModel)
class LearningAnnotationModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'agent', 'is_active', 'created_at', 'updated_at')
    list_editable = ('is_active',)
    list_filter = ('is_active', 'agent')
    search_fields = ('observation', 'suggestion', 'agent__name', 'agent__key')
    readonly_fields = ('id', 'created_at', 'updated_at')
    autocomplete_fields = ('agent',)
    list_select_related = ('agent',)

    def changelist_view(self, request, extra_context=None):
        try:
            with transaction.atomic():
                return super().changelist_view(request, extra_context=extra_context)
        except ValidationError as exc:
            self.message_user(request, ' '.join(exc.messages), level=messages.ERROR)
            return HttpResponseRedirect(request.get_full_path())


@admin.register(ConversationAnalysisModel)
class ConversationAnalysisAdmin(admin.ModelAdmin):
    """Seguimiento de análisis y reintentos, disponible solo para consulta."""

    list_display = ('batch', 'position', 'conversation', 'agent', 'status', 'attempts', 'next_attempt_at', 'updated_at')
    list_filter = ('status', 'agent', 'batch', 'position')
    search_fields = ('conversation__session_id', 'agent__key', 'agent__name', 'last_error')
    list_select_related = ('batch', 'conversation', 'agent', 'last_run')

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WeeklyAnalysisBatchModel)
class WeeklyAnalysisBatchAdmin(admin.ModelAdmin):
    """Consulta de los lotes semanales y de su consolidación final."""

    list_display = ('agent', 'week_start', 'week_end', 'status', 'phase', 'final_attempts', 'next_attempt_at', 'updated_at')
    list_filter = ('status', 'phase', 'agent')
    search_fields = ('agent__key', 'agent__name', 'last_error')
    list_select_related = ('agent', 'annotation', 'last_run')

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(AgentRunModel)
admin.site.register(AgentStepModel)
admin.site.register(AgentEventModel)
admin.site.register(PendingRunModel)
