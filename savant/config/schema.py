"""Module and pipeline elements configuration templates."""

import json
import pathlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Union

from omegaconf import MISSING, DictConfig, OmegaConf

from savant.base.pyfunc import PyFunc
from savant.utils.log import get_logger

logger = get_logger(__name__)


@dataclass
class FramePadding:
    """Pipeline processing frame parameters"""

    keep: bool = True
    """Whether to keep paddings on the output frame"""

    left: int = 0
    """Size of the left padding"""

    top: int = 0
    """Size of the top padding"""

    right: int = 0
    """Size of the right padding"""

    bottom: int = 0
    """Size of the bottom padding"""

    def __bool__(self):
        return bool(self.left or self.top or self.right or self.bottom)

    @property
    def width(self) -> int:
        """Total width of paddings."""
        return self.left + self.right

    @property
    def height(self) -> int:
        """Total height of paddings."""
        return self.top + self.bottom


@dataclass
class SourceShaper:
    """A class that will use an object implementing
    :py:class:`~savant.base.source_shaper.BaseSourceShaper` to define a source shape
    for each source.

    For example,

    .. code-block:: yaml

        - element: pyfunc
          module: module.source_shaper_example_module
          class_name: SourceShaperExampleClass
    """

    module: str
    """Module name to import."""

    class_name: str
    """Python class name to instantiate."""

    kwargs: Optional[Dict[str, Any]] = None
    """Keyword arguments for the shaper."""


@dataclass
class FrameParameters:
    """Pipeline processing frame parameters.

    .. note:: When ``shaper`` is specified, ``width``, ``height`` and ``padding``
    must be not set.
    """

    width: Optional[int] = None
    """Pipeline processing frame width"""

    height: Optional[int] = None
    """Pipeline processing frame height"""

    padding: Optional[FramePadding] = None
    """Add paddings to the frame before processing"""

    geometry_base: int = 8
    """Base value for frame parameters. 
    All frame parameters must be divisible by this value.
    """

    shaper: Optional[SourceShaper] = None
    """Custom source shaper definition."""


@dataclass
class FrameProcessingCondition:
    """Conditions for frame processing.
    If all conditions are met, frame will be processed.
    """

    tag: Optional[str] = None
    """Frame tag for filtering frames to be processed.

    If specified, frame will be processes only if it has the specified tag.
    If not specified, all frames will be processed.
    """


@dataclass
class BufferQueuesParameters:
    """Configure queues before and after pyfunc elements."""

    length: int = 10
    """Length of the queue in buffers (0 - no limit)."""

    byte_size: int = 0
    """Size of the queue in bytes (0 - no limit)."""


@dataclass
class TracingParameters:
    """Configure tracing.

    Example:
    .. code-block:: yaml

        sampling_period: 100
        append_frame_meta_to_span: False
        root_span_name: demo-pipeline-root
        provider: opentelemetry
        # or (mutually exclusive with provider_params, high priority)
        provider_params_config: /path/to/provider_params.json
        # or (mutually exclusive with provider_params_config, low priority)
        provider_params:
          service_name: demo-pipeline
          protocol: grpc
          endpoint: "http://jaeger:4317"
          timeout: 5000 # milliseconds
          tls:
            ca: /path/to/ca.crt
            identity:
                certificate: /path/to/client.crt
                key: /path/to/client.key
    """

    sampling_period: int = 100
    """Sampling period in frames."""

    append_frame_meta_to_span: bool = False
    """Append frame metadata to telemetry span."""

    root_span_name: Optional[str] = None
    """Name for root span."""

    provider: Optional[str] = None
    """Tracing provider name."""

    # TODO: remove provider_params in Savant 0.6
    provider_params: Optional[Dict[str, Any]] = None
    """Parameters for tracing provider."""

    provider_params_config: Optional[pathlib.Path] = None
    """JSON file path to the file with tracing provider parameters."""


@dataclass
class MetricsParameters:
    """Configure metrics.

    Example:
    .. code-block:: yaml

        frame_period: 1000
        time_period: 1
        history: 100
        extra_labels:
          label1: value1
          label2: value2
    """

    frame_period: Optional[int] = None
    """Output stats after every N frames."""

    time_period: Optional[int] = None
    """Output stats after every N seconds."""

    history: int = 100
    """How many last stats to keep in the memory."""

    extra_labels: Optional[Dict[str, str]] = None
    """Extra labels to add."""


@dataclass
class TelemetryParameters:
    """Configure telemetry.

    Example:
    .. code-block:: yaml

        tracing:
          sampling_period: 100
          append_frame_meta_to_span: False
          root_span_name: demo-pipeline-root
          provider: opentelemetry
          # or (mutually exclusive with provider_params, high priority)
          provider_params_config: /path/to/provider_params.json
          # or (mutually exclusive with provider_params_config, low priority)
          provider_params:
            service_name: demo-pipeline
            protocol: grpc
            endpoint: "http://jaeger:4317"
            timeout: 5000 # milliseconds
            tls:
              ca: /path/to/ca.crt
              identity:
                  certificate: /path/to/client.crt
                  key: /path/to/client.key
        metrics:
          frame_period: 1000
          time_period: 1
          history: 100
          provider: prometheus
          provider_params:
            port: 8000

    """

    tracing: TracingParameters = field(default_factory=TracingParameters)
    """Tracing configuration."""

    metrics: MetricsParameters = field(default_factory=MetricsParameters)
    """Metrics configuration."""


@dataclass
class PipelineElement:
    """Base pipeline element configuration template. Validates entries in a
    module config file under ``pipeline.source``, ``pipeline.elements`` and
    ``pipeline.sink``.

    Look for examples in :py:attr:`.element` documentation.
    """

    element: str = MISSING
    """Either a Gstreamer element name (gst factory name)
    or a short notation string to define :py:attr:`.element`, :py:attr:`.element_type`
    and :py:attr:`.version` at the same time.

    Short notation string format is ``<element>@<element_type>:<version>``

    .. note:: Version is ``v1`` by default.

    Examples.

    The following three element definitions are equivalent:

    .. code-block:: yaml

        - element: nvinfer
          element_type: attribute_model
          version: v1

        ...

        - element: nvinfer@attribute_model:v1

        ...

        - element: nvinfer@attribute_model

    Some elements might not have subtypes, in this case :py:attr:`.version` in the
    short notation can be defined immediately after the :py:attr:`.element`:

    .. code-block:: yaml

        - element: drawbin:v1
          location: /data/frames/image_%06d.jpg

    .. warning::

        Mixing short notation and full definition is not supported.

        Examples of unsupported notation mixing:

        .. code-block:: yaml

            - element: nvinfer@attribute_model
              version: v1

            ...

            - element: nvinfer:v1
              element_type: attribute_model

            ...

            - element: nvinfer
              element_type: attribute_model:v1
    """

    element_type: Optional[str] = None
    """Element type/subtype, can be defined as a substring of the :py:attr:`.element`.

    For example, ``detector`` in

    .. code-block:: yaml

        - element: nvinfer@detector
    """

    version: str = 'v1'
    """Element version, can be defined as a substring of the :py:attr:`.element`.

    For example, ``v1`` in

    .. code-block:: yaml

        - element: nvinfer@detector:v1
    """

    name: Optional[str] = None
    """GstElement instance name. Arbitrary string, useful for identifying
    pipeline elements.
    """

    properties: Dict[str, Any] = field(default_factory=dict)
    """GstElement properties."""

    @property
    def full_name(self):
        """Full element name."""
        return get_element_name(self)


def get_element_name(element: Union[DictConfig, PipelineElement]) -> str:
    """Returns the full name of the element, including the element factory,
    type, and name. The function is needed to get a uniform element name for
    different element representations (DictConfig/PipelineElement).

    :param element: Element.
    :return: Element name.
    """
    if isinstance(element, PipelineElement):
        element_config = OmegaConf.structured(element)
    else:
        element_config = element

    full_name = element_config.element

    if element_config.get('element_type'):
        full_name += f'@{element_config.element_type}'

    if element_config.get('version'):
        full_name += f':{element_config.version}'

    if element_config.get('name'):
        full_name += f'(name={element_config.name})'

    return full_name


@dataclass
class SourceElement(PipelineElement):
    """A pipeline element that produces pipeline input."""

    ingress_frame_filter: Optional[PyFunc] = None
    """Frame filter for ingress frames.

    .. note::

        Ingress filter can only be configured for ``zeromq_source_bin`` source.
    """

    def __post_init__(self):
        if self.element == 'zeromq_source_bin':
            if self.ingress_frame_filter is None:
                self.ingress_frame_filter = PyFunc(
                    module='savant.base.frame_filter',
                    class_name='DefaultIngressFilter',
                )
            kwargs = {}
            if 'kwargs' in self.properties and self.properties['kwargs']:
                kwargs = json.loads(self.properties['kwargs'])
            if self.ingress_frame_filter.kwargs:
                kwargs.update(self.ingress_frame_filter.kwargs)

            self.properties.update(
                {
                    'ingress-module': self.ingress_frame_filter.module,
                    'ingress-class': self.ingress_frame_filter.class_name,
                    'ingress-kwargs': json.dumps(kwargs),
                    'ingress-dev-mode': self.ingress_frame_filter.dev_mode,
                }
            )
        elif self.ingress_frame_filter:
            logger.warning(
                'Ingress filter is not supported for "%s", '
                'no filtering will be performed.',
                self.element,
            )


@dataclass
class SinkElement(PipelineElement):
    """A pipeline element that produces pipeline output."""

    egress_frame_filter: PyFunc = PyFunc(
        module='savant.base.frame_filter', class_name='DefaultEgressFilter'
    )
    """Frame filter for egress frames."""


@dataclass
class PyFuncElement(PipelineElement, PyFunc):
    """A pipeline element that will use an object implementing
    :py:class:`~savant.base.pyfunc.BasePyFuncPlugin` to apply custom processing to
    gstreamer buffers.

    For example,

    .. code-block:: yaml

        - element: pyfunc
          module: module.pyfunc_implementation_module
          class_name: PyFuncImplementationClass
    """

    element: str = 'pyfunc'
    """``"pyfunc"`` is the fixed gstreamer element class for PyFuncElement."""

    def __post_init__(self):
        super().__post_init__()
        kwargs = {}
        if 'kwargs' in self.properties and self.properties['kwargs']:
            kwargs = json.loads(self.properties['kwargs'])
        if self.kwargs:
            kwargs.update(self.kwargs)

        self.properties.update(
            {
                'module': self.module,
                'class': self.class_name,
                'kwargs': json.dumps(kwargs),
                'dev-mode': self.dev_mode,
            }
        )


@dataclass
class DrawFunc(PyFuncElement):
    """A pipeline element that will use an object implementing
    :py:class:`~savant.deepstream.base_drawfunc.BaseNvDsDrawFunc`
    to draw metadata on frames.

    .. note::

        Default values for :py:attr:`.module` and :py:attr:`.class_name` attributes
        are set to use :py:class:`~savant.deepstream.drawfunc.NvDsDrawFunc` drawfunc
        implementation.
    """

    module: str = 'savant.deepstream.drawfunc'
    """Module name to import."""

    class_name: str = 'NvDsDrawFunc'
    """Python class name to instantiate."""

    rendered_objects: Optional[Dict[str, Dict[str, Any]]] = None
    """A specification of objects to be rendered by the default draw function.

    For more details, look at :ref:`savant_101/90_draw_func:Declarative Configuration`.
    """

    condition: FrameProcessingCondition = field(
        default_factory=FrameProcessingCondition
    )
    """Conditions for filtering frames to be processed by the draw function.

    The draw function will be applied only to frames when all conditions are met.
    """

    def __post_init__(self):
        if self.kwargs is None:
            self.kwargs = {}
        self.kwargs.update(
            {
                'rendered_objects': self.rendered_objects,
                'condition': asdict(self.condition),
            }
        )
        super().__post_init__()


@dataclass
class ModelElement(PipelineElement):
    """A pipeline element that will run inference with specified deep learning
    model.

    For example,

    .. code-block:: yaml

        - element: nvinfer
          element_type: detector
          name: my_detector
          model:
              <model specification>
    """

    name: str = MISSING
    """GstElement instance name. Arbitrary string, useful for identifying
    pipeline elements. Mandatory for model elements.
    """

    model: Any = MISSING
    """Model configuration, any subclass of :py:class:`~savant.base.model.Model`.
    Check detailed Model hierarchy at links :ref:`base <model_hierarchy_base>`,
    :ref:`nvinfer <model_hierarchy_nvinfer>`.
    """


@dataclass
class GroupCondition:
    """Determines if a group should be loaded into the pipeline.
    If expr evaluates to the value, then the condition is considered to be true
    and the Group is enabled.
    """

    expr: str = MISSING
    """Expression to evaluate."""

    value: str = MISSING
    """Value to compare with."""

    @property
    def is_enabled(self) -> bool:
        """Returns True if the condition is enabled, False otherwise."""
        return self.expr == self.value


@dataclass
class ElementGroup:
    """Pipeline elements wrapper that can be used to add a condition
    on whether to load the group into the pipeline.
    """

    name: Optional[str] = None
    """Group name."""

    init_condition: GroupCondition = MISSING
    """Group init condition, mandatory."""

    elements: List[PipelineElement] = field(default_factory=list)
    """List of group elements.
    Can be a :py:class:`PipelineElement` or any subclass of it."""


@dataclass
class Pipeline:
    """Pipeline configuration template. Validates entries in a module config
    file under ``pipeline``. For example,

    .. code-block:: yaml

        pipeline:
            source:
                element: uridecodebin
                properties:
                    uri: file:///data/test.mp4
            elements:
            # user-defined pipeline elements or element groups
                - element: nvinfer@detector
                - group:
                    init_condition:
                      expr: expression
                      value: value
                    elements:
                      - element: nvinfer@detector
            sink:
                - element: console_sink
    """

    # TODO: Add format, e.g. NvDs

    source: SourceElement = MISSING
    """The source element of a pipeline."""

    # Union[] is not supported -> Any
    elements: List[Any] = field(default_factory=list)
    """Main Pipeline contents. Can include :py:class:`PipelineElement`
    or :py:class:`ElementGroup` nodes.
    """

    sink: List[SinkElement] = field(default_factory=list)
    """Sink elements of a pipeline."""

    pipeline_class: Optional[str] = None
    """Pipeline class name with full module path, 
    e.g. ``savant.deepstream.pipeline.NvDsPipeline``. 
    If not set, the default pipeline class specified in the entrypoint will be used.
    """

    runner_class: Optional[str] = None
    """Pipeline runner class name with full module path,
    e.g. ``savant.deepstream.runner.NvDsPipelineRunner``.
    If not set, the default runner class specified in the entrypoint will be used.
    """


@dataclass
class Module:
    """Module configuration template. Top-level configuration entity, validates
    all entries in a module config file. For example,

    .. code-block:: yaml

        name: module_name
        parameters:
        pipeline:
            source:
                element: uridecodebin
                properties:
                    uri: file:///data/test.mp4
            elements:
            sink:
                - element: console_sink
    """

    name: str
    """Module name."""

    parameters: Dict[str, Any] = field(default_factory=dict)
    """Module parameters."""

    pipeline: Pipeline = MISSING
    """Pipeline configuration.
    """
