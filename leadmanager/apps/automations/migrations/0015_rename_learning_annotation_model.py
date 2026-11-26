"""
Renombra LearningAnotations a LearningAnnotationModel, y sus campos
observaciones -> observation y sugerencias -> suggestion.

Solo renombra: la tabla y las columnas conservan sus datos, y la llave
foránea de WeeklyAnalysisBatchModel.annotation se ajusta sola. Se escribió a
mano porque makemigrations sin preguntas no detecta renombres y generaría
un borrado y una creación, perdiendo las anotaciones existentes.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('automations', '0014_weekly_analysis_batches'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='LearningAnotations',
            new_name='LearningAnnotationModel',
        ),
        migrations.RenameField(
            model_name='learningannotationmodel',
            old_name='observaciones',
            new_name='observation',
        ),
        migrations.RenameField(
            model_name='learningannotationmodel',
            old_name='sugerencias',
            new_name='suggestion',
        ),
    ]
