from typing import Optional

from savant.client.log_provider import LogProvider
from savant.client.runner.sink import AsyncSinkRunner, SinkRunner
from savant.utils.log import get_logger

logger = get_logger(__name__)


class SinkBuilder:
    """Builder for Sink.

    Usage example:

    .. code-block:: python

        sink = (
            SinkBuilder()
            .with_socket('rep+connect:ipc:///tmp/zmq-sockets/output-video.ipc')
            .with_idle_timeout(60)
            .with_log_provider(JaegerLogProvider('http://localhost:16686'))
            # Note: healthcheck port should be configured in the module.
            .with_module_health_check_url('http://172.17.0.1:8888/status')
            .build()
        )
        for result in sink:
            print(result.frame_meta)
            result.logs().pretty_print()

    Usage example (async):

    .. code-block:: python

        sink = (
            SinkBuilder()
            .with_socket('rep+connect:ipc:///tmp/zmq-sockets/output-video.ipc')
            .with_idle_timeout(60)
            .with_log_provider(JaegerLogProvider('http://localhost:16686'))
            .build_async()
        )
        async for result in sink:
            print(result.frame_meta)
            result.logs().pretty_print()
    """

    def __init__(
        self,
        socket: Optional[str] = None,
        log_provider: Optional[LogProvider] = None,
        idle_timeout: Optional[int] = None,
        module_health_check_url: Optional[str] = None,
        module_health_check_timeout: float = 60,
        module_health_check_interval: float = 5,
        source_id: Optional[str] = None,
        source_id_prefix: Optional[str] = None,
    ):
        self._socket = socket
        self._log_provider = log_provider
        self._idle_timeout = idle_timeout
        self._module_health_check_url = module_health_check_url
        self._module_health_check_timeout = module_health_check_timeout
        self._module_health_check_interval = module_health_check_interval
        self._source_id = source_id
        self._source_id_prefix = source_id_prefix

    def with_socket(self, socket: str) -> 'SinkBuilder':
        """Set ZeroMQ socket for Sink."""
        return self._with_field('socket', socket)

    def with_log_provider(self, log_provider: LogProvider) -> 'SinkBuilder':
        """Set log provider for Sink."""
        return self._with_field('log_provider', log_provider)

    def with_idle_timeout(self, idle_timeout: Optional[int]) -> 'SinkBuilder':
        """Set idle timeout for Sink.

        Sink will stop trying to receive a message from ZeroMQ socket when it
        did not receive a message for idle_timeout seconds.
        """
        return self._with_field('idle_timeout', idle_timeout)

    def with_module_health_check_url(self, url: str) -> 'SinkBuilder':
        """Set module health check url for Sink.

        Sink will check the module health before receiving any messages.
        """
        return self._with_field('module_health_check_url', url)

    def with_module_health_check_timeout(self, timeout: float) -> 'SinkBuilder':
        """Set module health check timeout for Sink.

        Sink will wait for the module to be ready for the specified timeout.
        """
        return self._with_field('module_health_check_timeout', timeout)

    def with_module_health_check_interval(self, interval: float) -> 'SinkBuilder':
        """Set module health check interval for Sink.

        Sink will check the module health every specified interval.
        """
        return self._with_field('module_health_check_interval', interval)

    def with_source_id(self, source_id: Optional[str]) -> 'SinkBuilder':
        """Set source id for Sink.

        Sink will filter messages by source id.
        """
        return self._with_field('source_id', source_id)

    def with_source_id_prefix(self, source_id_prefix: Optional[str]) -> 'SinkBuilder':
        """Set source id prefix for Sink.

        Sink will filter messages by source id prefix.

        Note: source_id and source_id_prefix are mutually exclusive. If both are set, source_id will be used.
        """
        return self._with_field('source_id_prefix', source_id_prefix)

    def build(self) -> SinkRunner:
        """Build Sink."""

        assert self._socket is not None, 'socket is required'
        logger.debug(
            'Building sink with socket %s and log provider %s.',
            self._socket,
            self._log_provider,
        )
        return SinkRunner(
            socket=self._socket,
            log_provider=self._log_provider,
            idle_timeout=self._idle_timeout,
            module_health_check_url=self._module_health_check_url,
            module_health_check_timeout=self._module_health_check_timeout,
            module_health_check_interval=self._module_health_check_interval,
            source_id=self._source_id,
            source_id_prefix=self._source_id_prefix,
        )

    def build_async(self) -> AsyncSinkRunner:
        """Build async Sink."""

        assert self._socket is not None, 'socket is required'
        logger.debug(
            'Building async sink with socket %s and log provider %s.',
            self._socket,
            self._log_provider,
        )
        return AsyncSinkRunner(
            socket=self._socket,
            log_provider=self._log_provider,
            idle_timeout=self._idle_timeout,
            module_health_check_url=self._module_health_check_url,
            module_health_check_timeout=self._module_health_check_timeout,
            module_health_check_interval=self._module_health_check_interval,
            source_id=self._source_id,
            source_id_prefix=self._source_id_prefix,
        )

    def __repr__(self):
        return (
            f'SinkBuilder('
            f'socket={self._socket}, '
            f'log_provider={self._log_provider}, '
            f'idle_timeout={self._idle_timeout}, '
            f'module_health_check_url={self._module_health_check_url}, '
            f'module_health_check_timeout={self._module_health_check_timeout}, '
            f'module_health_check_interval={self._module_health_check_interval}, '
            f'source_id={self._source_id}, '
            f'source_id_prefix={self._source_id_prefix})'
        )

    def _with_field(self, field: str, value) -> 'SinkBuilder':
        return SinkBuilder(
            **{
                'socket': self._socket,
                'log_provider': self._log_provider,
                'idle_timeout': self._idle_timeout,
                'module_health_check_url': self._module_health_check_url,
                'module_health_check_timeout': self._module_health_check_timeout,
                'module_health_check_interval': self._module_health_check_interval,
                'source_id': self._source_id,
                'source_id_prefix': self._source_id_prefix,
                field: value,
            }
        )
