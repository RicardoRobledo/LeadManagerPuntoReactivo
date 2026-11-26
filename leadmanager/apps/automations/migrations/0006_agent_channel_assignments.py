from django.db import migrations, models
import django.db.models.deletion


def migrate_channels(apps, schema_editor):
    Agent = apps.get_model('automations', 'AgentModel')
    Assignment = apps.get_model('automations', 'AgentChannelAssignment')
    aliases = {
        'chatweb': 'web', 'web': 'web', 'formulario': 'form', 'form': 'form',
        'correo': 'email', 'email': 'email', 'linkedin': 'linkedin',
        'whatsapp': 'whatsapp', 'messenger': 'messenger', 'instagram': 'instagram',
    }
    for agent in Agent.objects.using(schema_editor.connection.alias).all().order_by('pk'):
        seen = set()
        for label in agent.channels or []:
            channel = aliases.get(str(label).strip().casefold())
            if channel is None:
                raise ValueError(f'Canal desconocido para el agente {agent.key}: {label}')
            if channel not in seen:
                Assignment.objects.using(schema_editor.connection.alias).create(
                    agent_id=agent.pk, channel=channel, active=agent.is_active,
                )
                seen.add(channel)


class Migration(migrations.Migration):
    dependencies = [('automations', '0005_agent_learning_and_annotations')]
    operations = [
        migrations.CreateModel(
            name='AgentChannelAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel', models.CharField(choices=[('web', 'chatweb'), ('form', 'Formulario'), ('email', 'Correo'), ('linkedin', 'LinkedIn'), ('whatsapp', 'WhatsApp'), ('messenger', 'Messenger'), ('instagram', 'Instagram')], max_length=20)),
                ('active', models.BooleanField(default=False)),
                ('agent', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='channel_assignments', to='automations.agentmodel')),
            ],
            options={'ordering': ('channel',), 'constraints': [
                models.UniqueConstraint(fields=('agent', 'channel'), name='unique_agent_channel'),
                models.UniqueConstraint(condition=models.Q(active=True), fields=('channel',), name='unique_active_agent_channel'),
            ]},
        ),
        migrations.RunPython(migrate_channels, migrations.RunPython.noop),
    ]
