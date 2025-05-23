Module Overview
===============

A module is an executable unit that is deployed and executed on Nvidia edge devices or in the cloud on x86 servers with discrete GPUs. The module is defined in a YAML configuration file.

The module is responsible for managing every activity displayed in the following picture:

.. image:: ../_static/img/1_pipeline_diagram.jpg

Module Runtime
--------------

Modules are executed within specially prepared docker containers. If a module does not require any additional dependencies, a base Savant docker image may be used to run it. Otherwise, a customized container must be built based on the selected base image. We provide base images for:

* Nvidia DGPUs on x86 architecture

   .. code-block:: bash

        docker pull ghcr.io/insight-platform/savant-deepstream:latest

* Deepstream 7.0 capable Nvidia edge devices (Jetson AGX Orin, Orin NX, Orin Nano)

   .. code-block:: bash

        docker pull ghcr.io/insight-platform/savant-deepstream-l4t:latest

The module utilizes the following directories:

* ``/models`` - where compiled models are located;
* ``/downloads`` - where the module downloads models from remote locations;
* ``/opt/savant`` - where the module expects the application code root is.

Normally, you map host's directories to the above-mentioned paths. You also can override ``/downloads`` and ``/models`` with the following parameters:

.. code-block:: yaml

  model_path: ${oc.env:MODEL_PATH, /models}
  download_path: ${oc.env:DOWNLOAD_PATH, /downloads}


Module Configuration
--------------------

Every module must have a ``name``, which is an arbitrary string, the ``pipeline`` block, and ``parameters``.

Parameters
^^^^^^^^^^

Any number of :py:attr:`~savant.config.schema.Module.parameters` can be set in the ``parameters`` section of the module configuration file, including user-defined ones.

If you need to define a per-stream configuration, consider using external configuration options like Etcd or a database like Redis or MongoDB. Another option could be passing the required instructions in the Savant protocol with frame tags enabling precise per-frame configuration.

The following parameters are defined for a Savant module by default:

.. literalinclude:: ../../../savant/config/default.yml
  :language: YAML
  :lines: 1-198

.. note::

  Any config values mentioning ``$``-based expressions (like ``${name}``) are substituted with the literal defined for the ``name``.

To access parameter values in runtime use the :py:class:`~savant.parameter_storage.param_storage` function:

.. code-block:: python

  from savant.parameter_storage import param_storage


  parameter_value = param_storage()['parameter_name']


Dynamic Parameters
^^^^^^^^^^^^^^^^^^

Savant supports module-wide dynamic parameters. Those parameters can retrieve their current values during the module execution. Currently, only Etcd is supported as a dynamic parameter source. The Etcd connection is configured in the ``parameters.etcd`` section.

.. code-block:: yaml

      etcd:
        hosts: [127.0.0.1:2379]
        connect_timeout: 5
        watch_path: 'savant'

The dynamic parameters are available in the functions via ``eval_expr``. Example of getting the value for ``savant/some_dynamic_parameter_name`` from Etcd:

.. code-block:: python

  from savant_rs.utils import eval_expr


  parameter_value, _ = eval_expr('etcd("some_dynamic_parameter_name", "default_value")')


Output Queue Max Size
^^^^^^^^^^^^^^^^^^^^^

The ``queue_maxsize`` parameter specifies the size of the buffer located at the end of the pipeline, right before the ZeroMQ sink. The parameter may be beneficial in cases when payload metadata fluctuates between sequential frames in size significantly, causing temporary output delays. The buffer helps avoid blocking the pipeline while it has spare capacity. The default value is ``100``.

To configure the custom value, use:

.. code-block:: yaml

    parameters:
      queue_maxsize: 100

.. warning::

    Large values cause extra GPU/CPU memory usage.

.. _buffering_queues:

Buffering Queues
^^^^^^^^^^^^^^^^

The ``buffer_queues`` parameter is used to enable Python parallelization and enhance the performance in compute-intensive pipelines. By default, the parameter is disabled.

Read about the parameter in :doc:`/recipes/1_python_multithreading`.

.. _deepstream_buffering_configuration:

Nvidia Stream Muxer and Converter Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Nvidia Stream Muxer** allocates a fixed number of buffers for batches. By default, it is equal to ``4``. Sometimes it is not enough when developer uses queues in the pipeline. Before the pipeline does not release an allocated buffer, which means that no one pipeline element works with it, stream muxer is not able to process the incoming data. Thus, the pipeline can idle. To avoid that you can use the parameter to extend the number of such buffers.

.. code-block:: yaml

    parameters:
      muxer_buffer_pool_size: 16


You specify the number in batches.

**Nvidia Stream Converter** also allocates a fixed number of buffers for frames. By default, it is equal to ``4``. These frames are consumed by **Nvidia Stream Muxer**. To guarantee that the muxer can fill its buffers, the converter must provide enough of them.

In short, the following constraint must be satisfied:

.. code-block::

    Number_Of_Streams x Converter_Allocated_Buffers >= Batch_size x Muxer_Allocated_Buffers

    E.g.,

    4 streams x 8 converter buffers >= 4 frames per batch * 8 muxer buffers
                                 32 >= 32


To configure the parameter, use:

.. code-block:: yaml

    parameters:
      stream_buffer_pool_size: 32


You specify the number in frames.

.. note::

    Remember, that buffers occupy GPU RAM, so plan them carefully.

Log Level
^^^^^^^^^

The ``log_level`` parameter defines the verbosity of logging for the framework. By default, it is configured as ``log_level: ${oc.env:LOGLEVEL, 'INFO'}`` which allows overriding it with the ``LOGLEVEL`` environment variable.

.. note::

  Savant uses Rust's `env_logger <https://docs.rs/env_logger/latest/env_logger/>`_-based logging configured through ``LOGLEVEL`` environment variable. Savant's python logging is setup to accept the same logging configuration format and to pass the messages down to the underlying Rust logger. As such, for logging to be enabled, the ``LOGLEVEL`` environment variable must be set to a non-empty value. Therefore, it is recommended not to change the default ``log_level`` module configuration parameter value and to use the ``LOGLEVEL`` environment variable for logging configuration instead.

The ``log_level`` parameter value is expected to be in the Rust's `env_logger <https://docs.rs/env_logger/latest/env_logger/>`_ format, i.e. a comma-separated list of logging directives of the form ``target=level``.

Possible logging levels are:

* ``trace``
* ``debug``
* ``info``
* ``warn``
* ``error``

The ``target`` is the name of the logging target, which is typically a ``::``-separated path to the module. All Savant's messages are logged under the ``insight::savant`` prefix, so setting ``LOGLEVEL`` to, for example, ``insight::savant=info`` is enough to enable logging for all Savant's modules.

Some examples of logging configuration:

* ``info`` - turns on all info logging
* ``insight::savant=info`` - turns on all info logging for Savant's modules
* ``warn,insight::savant::custom_pyfunc=debug`` - turns on global warn logging and debug logging for the ``custom_pyfunc`` module
* ``insight::savant::custom_pyfunc=debug`` - turns on debug logging for the ``custom_pyfunc`` and disables all other logging sources

Output Video Stream Codec
^^^^^^^^^^^^^^^^^^^^^^^^^

If the ``output_frame`` section is set, Savant adds encoded video streams to sink. More information you will find in the next section :doc:`12_video_processing`.

OpenTelemetry Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^

The ``telemetry.tracing`` section defines the telemetry configuration. The ``endpoint`` in (``telemetry.tracing.provider_params`` or ``telemetry.tracing.provider_params_config``) is required when ``telemetry.tracing.provider`` is set to ``'opentelemetry'``.

Example:

.. code-block:: yaml

    telemetry:
      tracing:
        sampling_period: 100
        append_frame_meta_to_span: false
        root_span_name: demo-pipeline-root
        provider: opentelemetry
        # or (mutually exclusive with provider_params, high priority)
        # use provider config file (take a look at samples/telemetry/otlp/x509_provider_config.json)
        provider_params_config: /path/to/x509_provider_config.json
        # or (mutually exclusive with provider_params_config, low priority)
        # use provider config attributes
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

Read more on OpenTelemetry in :doc:`/advanced_topics/9_open_telemetry`.

Metrics Collection Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The ``telemetry.metrics`` section defines the metrics collection configuration. The ``port`` in ``telemetry.metrics.provider_params`` is required when ``telemetry.metrics.provider`` is set to ``'prometheus'``. ``labels`` in ``telemetry.metrics.provider_params`` defines extra labels added to the metrics.

Example:

.. code-block:: yaml

    telemetry:
      metrics:
        frame_period: 1000
        time_period: 1
        history: 100
        extra_labels:
          module_type: detector

DevServer Configuration
^^^^^^^^^^^^^^^^^^^^^^^

DevServer is a special module execution mode enabling change detection in custom Python code and reloading those pieces automatically without the need for container restarts.

Read more on DevServer configuration in :doc:`/advanced_topics/9_dev_server`.

Pipeline Shutdown Authentication
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The ``shutdown_auth`` parameter defines a secret token which can be sent in the service shutdown message to terminate the pipeline. By default ``shutdown_auth`` is unset, and the pipeline ignores shutdown messages.

Currently, shutdown messages can be sent with :doc:`Client SDK </advanced_topics/10_client_sdk>`.

Pipeline
--------

The pipeline section is where the processing steps are defined. In the section, detectors, classifiers, segmenting units, and custom Python units are placed. They are described in detail in the following sections.
