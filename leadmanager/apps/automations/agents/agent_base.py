import logging
import traceback

import openai

from django.conf import settings
from django.utils import timezone

from typing import Literal
from pydantic import BaseModel, Field

from leadmanager.apps.automations.models import AgentEventModel, AgentModel, AgentRunModel, AgentStepModel


logger = logging.getLogger(__name__)


class StepExecutionError(Exception):
    """Se lanza cuando un step falla; envuelve la excepción original y
    detiene la ejecución del resto del pipeline."""

    def __init__(self, step_name, original_exception):
        self.step_name = step_name
        self.original_exception = original_exception
        super().__init__(f"Error en step '{step_name}': {original_exception}")


class Agent:
    """
    Unit with its own fixed flow of steps.

    ``flow`` is the ordered list of steps that run when the
    agent is executed. ``tools`` is a separate catalog of
    functions the agent (or an LLM) may call later; it is not
    run automatically. Agents communicate through the shared
    context ``ctx``.
    """

    def __init__(self, name, mode='production', shadow_steps=None, production_steps=None, *, steps=None):
        self.name = name
        self.mode = mode
        self.shadow_steps = shadow_steps if shadow_steps is not None else []
        self.production_steps = production_steps if production_steps is not None else (steps if steps is not None else [])
        self.steps = self.production_steps

    def run(self, ctx, *, run=None):

        if run is None:
            run = AgentRunModel.objects.create(
                agent_name=self.name,
                mode=self.mode,
                source=ctx.get('source', ''),
            )
        ctx['_agent_run'] = run
        self._link_conversation(run, ctx)

        steps = self.shadow_steps if self.mode == 'shadow_testing' else self.production_steps

        try:
            if self.mode == AgentModel.ModeEnum.SHADOW_TESTING and not steps:
                raise ValueError('El agente de pruebas no tiene pasos configurados.')
            for order, step in enumerate(steps, start=1):
                self._run_step(run, order, step, ctx)
            if self.mode == AgentModel.ModeEnum.SHADOW_TESTING:
                run.result = ctx['_shadow_result']
        except Exception:
            run.status = AgentRunModel.StatusEnum.FAILED
            run.finished_at = timezone.now()
            run.save(update_fields=['status', 'finished_at'])
            raise

        run.status = AgentRunModel.StatusEnum.SUCCESS
        run.finished_at = timezone.now()
        fields = ['status', 'finished_at']
        if self.mode == AgentModel.ModeEnum.SHADOW_TESTING:
            fields.append('result')
        run.save(update_fields=fields)

        return ctx

    def _run_step(self, run, order, step, ctx):

        step_name = step.__name__
        step_record = AgentStepModel.objects.create(
            run=run, step_name=step_name, order=order
        )
        ctx['_current_step'] = step_record

        try:
            step(self, ctx)
        except Exception as exc:
            step_record.status = AgentStepModel.StatusEnum.FAILED
            step_record.finished_at = timezone.now()
            step_record.exception_type = type(exc).__name__
            step_record.error_message = str(exc)
            step_record.traceback = traceback.format_exc()
            step_record.save(update_fields=[
                'status', 'finished_at', 'exception_type', 'error_message', 'traceback'
            ])

            # Si el step que falló ya había dejado la conversación en ctx,
            # la enlazamos igual para no perder el vínculo del run fallido.
            self._link_conversation(run, ctx)

            logger.exception(
                "Agent '%s' detenido en step '%s'", self.name, step_name
            )
            raise StepExecutionError(step_name, exc) from exc

        step_record.status = AgentStepModel.StatusEnum.SUCCESS
        step_record.finished_at = timezone.now()
        step_record.save(update_fields=['status', 'finished_at'])

        self._link_conversation(run, ctx)

    def _link_conversation(self, run, ctx):
        """El run se crea antes del primer step, cuando todavía no existe la
        conversación (la crea verify_existing_conversation_step). En cuanto
        algún step la deja en ctx, la enlazamos al run — una sola vez."""

        conversation = ctx.get('conversation')

        if conversation is not None and run.conversation_id is None:
            run.conversation = conversation
            run.save(update_fields=['conversation'])

    def log_event(self, ctx, message, level=AgentEventModel.LevelEnum.INFO, data=None):
        """
        Logs an event in the agent's execution.
        """

        AgentEventModel.objects.create(
            run=ctx.get('_agent_run'),
            step=ctx.get('_current_step'),
            level=level,
            message=message,
            data=data,
        )

    def create_model_data(self, system_prompt, messages, model, **kwargs):
        """
        Creates the data dictionary for the OpenAI Responses API.

        :param system_prompt: System prompt.
        :param messages: Messages to send to the model.
        :param kwargs: Additional arguments for the OpenAI Responses API.
        :return: Data dictionary for the OpenAI Responses API.
        """

        return {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                *messages
            ],
            **kwargs
        }

    def response_model(self, system_prompt, input, type='raw', model='gpt-5.6-luna', **kwargs):
        """
        Returns the response model for the agent's output.

        :param system_prompt: System prompt.
        :param input: Input to send to the model.
        :param type: Response type ('parsed' or 'raw').
        :param model: The model to use for the response.
        :param kwargs: Additional arguments for the OpenAI Responses API.
        :return: The response from the model.
        """

        response = None

        prompt_data = self.create_model_data(
            system_prompt, input, model=model, **kwargs)

        if type == "parsed":
            response = openai.responses.parse(**prompt_data)
        else:
            response = openai.responses.create(**prompt_data)

        return response
