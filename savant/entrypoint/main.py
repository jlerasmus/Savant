"""Module entrypoints.
isort:skip_file
"""

import importlib
import os
import signal
from pathlib import Path
from typing import IO, Any, Union, Type

from savant.gstreamer import Gst  # should be first
from savant.config import ModuleConfig
from savant.config.schema import ElementGroup, ModelElement, Module
from savant.deepstream.encoding import check_encoder_is_available
from savant.deepstream.nvinfer.build_engine import build_engine
from savant.deepstream.nvinfer.model import NvInferModel
from savant.deepstream.pipeline import NvDsPipeline
from savant.deepstream.runner import NvDsPipelineRunner
from savant.gstreamer.codecs import AUXILIARY_STREAM_CODECS
from savant.healthcheck.status import set_module_status, ModuleStatus
from savant.utils.check_display import check_display_env
from savant.utils.log import get_logger, init_logging, update_logging
from savant.utils.sink_factories import sink_factory
from savant.utils.welcome import get_starting_message


def main(module_config: Union[str, Path, IO[Any]]):
    """Runs NvDsPipeline based module.

    :param module_config: Module configuration.
    """

    # To gracefully shut down the adapter on SIGTERM (raise KeyboardInterrupt)
    signal.signal(signal.SIGTERM, signal.getsignal(signal.SIGINT))

    status_filepath = os.environ.get('SAVANT_STATUS_FILEPATH')
    if status_filepath is not None:
        status_filepath = Path(status_filepath)
        if status_filepath.exists():
            status_filepath.unlink()
        status_filepath.parent.mkdir(parents=True, exist_ok=True)
        set_module_status(status_filepath, ModuleStatus.INITIALIZING)

    # load default.yml and set up logging
    config = ModuleConfig().config

    init_logging(config.parameters['log_level'])
    logger = get_logger('main')
    logger.info(get_starting_message('module'))

    # load module config
    config: Module = ModuleConfig().load(module_config)

    PipelineClass: Type[NvDsPipeline] = NvDsPipeline
    if config.pipeline.pipeline_class:
        pipeline_class_path = config.pipeline.pipeline_class.split('.')
        pipeline_module = importlib.import_module('.'.join(pipeline_class_path[:-1]))
        PipelineClass = getattr(pipeline_module, pipeline_class_path[-1])

    RunnerClass: Type[NvDsPipelineRunner] = NvDsPipelineRunner
    if config.pipeline.runner_class:
        runner_class_path = config.pipeline.runner_class.split('.')
        runner_module = importlib.import_module('.'.join(runner_class_path[:-1]))
        RunnerClass = getattr(runner_module, runner_class_path[-1])

    # reconfigure savant logger with updated loglevel
    update_logging(config.parameters['log_level'])

    check_display_env(logger)

    # possible exceptions will cause app to crash and log error by default
    # no need to handle exceptions here
    sink = sink_factory(config.pipeline.sink)

    Gst.init(None)

    output_frame = config.parameters.get('output_frame')
    if output_frame and not check_encoder_is_available(output_frame):
        return False

    auxiliary_encoders = config.parameters.get('auxiliary_encoders')
    if auxiliary_encoders:
        for aux_encoder in auxiliary_encoders:
            if not check_encoder_is_available(
                aux_encoder,
                allowed_codecs=[x.value.name for x in AUXILIARY_STREAM_CODECS],
            ):
                return False

    pipeline = PipelineClass(
        config.name,
        config.pipeline,
        **config.parameters,
    )

    try:
        with RunnerClass(pipeline, status_filepath) as runner:
            try:
                for msg in pipeline.stream():
                    sink(msg, **dict(module_name=config.name))
            except KeyboardInterrupt:
                logger.info('Shutting down module "%s".', config.name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error(exc, exc_info=True)
                # TODO: Sometimes pipeline hangs when exit(1) or not exit at all is called.
                #       E.g. when the module has "req+connect" socket at the sink and
                #       sink adapter is not available.
                os._exit(1)  # pylint: disable=protected-access
    except Exception as exc:  # pylint: disable=broad-except
        logger.error('Module "%s" error %s', config.name, exc, exc_info=True)
        exit(1)

    if runner.error is not None:
        exit(1)


def build_module_engines(module_config: Union[str, Path, IO[Any]]):
    """Builds module model's engines.

    :param module_config: Module configuration.
    """

    # To gracefully shut down the adapter on SIGTERM (raise KeyboardInterrupt)
    signal.signal(signal.SIGTERM, signal.getsignal(signal.SIGINT))

    # load default.yml and set up logging
    config = ModuleConfig().config

    init_logging(config.parameters['log_level'])
    logger = get_logger('engine_builder')
    logger.info(get_starting_message('module engine builder'))

    # load module config
    config = ModuleConfig().load(module_config)

    # reconfigure savant logger with updated loglevel
    update_logging(config.parameters['log_level'])

    check_display_env(logger)

    nvinfer_elements = []
    for element in config.pipeline.elements:
        if isinstance(element, ModelElement):
            if isinstance(element.model, NvInferModel):
                nvinfer_elements.append(element)

        elif isinstance(element, ElementGroup):
            if element.init_condition.is_enabled:
                for group_element in element.elements:
                    if isinstance(group_element, ModelElement):
                        if isinstance(group_element.model, NvInferModel):
                            nvinfer_elements.append(group_element)

    if not nvinfer_elements:
        logger.error('No model elements found.')
        exit(1)

    Gst.init(None)
    logger.info('GStreamer initialization done.')

    for element in nvinfer_elements:
        logger.info('Start building of the "%s" model engine.', element.name)
        try:
            build_engine(element)
            logger.info(
                'Successfully complete the engine building of the "%s" model.',
                element.name,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(
                'Failed to build the model "%s" engine: %s',
                element.name,
                exc,
                exc_info=True,
            )
            exit(1)
