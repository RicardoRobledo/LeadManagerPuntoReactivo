from django.db import migrations, models


def normalize_legacy_categories(apps, schema_editor):
    Agent = apps.get_model('automations', 'AgentModel')
    agents = Agent.objects.using(schema_editor.connection.alias)
    agents.filter(category='Asistente', key='conversation_analyzer').update(category='Análisis')
    agents.filter(category='Asistente').update(category='Prospección')
    invalid = list(agents.exclude(category__in=('Prospección', 'Análisis')).values_list('category', flat=True).distinct())
    if invalid:
        raise ValueError(f'Hay categorías de agentes desconocidas; revisa los registros antes de migrar: {invalid}')


class Migration(migrations.Migration):
    dependencies = [('automations', '0006_agent_channel_assignments')]
    operations = [
        migrations.RunPython(normalize_legacy_categories, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='agentmodel', name='category',
            field=models.CharField(choices=[('Prospección', 'Prospección'), ('Análisis', 'Análisis')], default='Prospección', max_length=80, verbose_name='Categoría'),
        ),
    ]
