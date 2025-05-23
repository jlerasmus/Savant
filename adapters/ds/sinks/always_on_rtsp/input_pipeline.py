from pygstsavantframemeta import gst_buffer_get_savant_frame_meta

from adapters.ds.sinks.always_on_rtsp.config import Config
from adapters.ds.sinks.always_on_rtsp.last_frame import LastFrameRef
from adapters.ds.sinks.always_on_rtsp.pipeline import add_elements
from savant.config.schema import PipelineElement
from savant.deepstream.decoding import configure_low_latency_decoding
from savant.gstreamer import Gst
from savant.gstreamer.codecs import caps_to_codec
from savant.gstreamer.element_factory import GstElementFactory
from savant.utils.log import get_logger

LOGGER_NAME = 'adapters.ao_sink.input_pipeline'
logger = get_logger(LOGGER_NAME)


def log_frame_metadata(pad: Gst.Pad, info: Gst.PadProbeInfo, config: Config):
    buffer: Gst.Buffer = info.get_buffer()
    savant_frame_meta = gst_buffer_get_savant_frame_meta(buffer)
    if savant_frame_meta is None:
        logger.warning(
            'Source %s. No Savant Frame Metadata found on buffer with PTS %s.',
            config.source_id,
            buffer.pts,
        )
        return Gst.PadProbeReturn.PASS

    video_frame, _ = config.video_pipeline.get_independent_frame(savant_frame_meta.idx)
    config.video_pipeline.delete(savant_frame_meta.idx)
    metadata_json = video_frame.json
    if config.metadata_output == 'logger':
        logger.info('Frame metadata: %s', metadata_json)
    else:
        print(f'Frame metadata: {metadata_json}')
    return Gst.PadProbeReturn.OK


def delete_frame_from_pipeline(pad: Gst.Pad, info: Gst.PadProbeInfo, config: Config):
    buffer: Gst.Buffer = info.get_buffer()
    savant_frame_meta = gst_buffer_get_savant_frame_meta(buffer)
    if savant_frame_meta is None:
        logger.warning(
            'Source %s. No Savant Frame Metadata found on buffer with PTS %s.',
            config.source_id,
            buffer.pts,
        )
        return Gst.PadProbeReturn.PASS

    config.video_pipeline.delete(savant_frame_meta.idx)
    return Gst.PadProbeReturn.OK


def on_decodebin_element_added(
    decodebin: Gst.Element,
    new_element: Gst.Element,
    config: Config,
):
    logger.debug('Added element %s to %s', new_element.get_name(), decodebin.get_name())
    if config.low_latency_decoding:
        configure_low_latency_decoding(new_element)


def link_added_pad(
    element: Gst.Element,
    src_pad: Gst.Pad,
    sink_pad: Gst.Pad,
):
    logger.debug(
        'Linking %s.%s to %s.%s',
        src_pad.get_parent().get_name(),
        src_pad.get_name(),
        sink_pad.get_parent().get_name(),
        sink_pad.get_name(),
    )

    assert src_pad.link(sink_pad) == Gst.PadLinkReturn.OK


def on_demuxer_pad_added(
    element: Gst.Element,
    src_pad: Gst.Pad,
    config: Config,
    pipeline: Gst.Pipeline,
    factory: GstElementFactory,
    sink_pad: Gst.Pad,
):
    caps: Gst.Caps = src_pad.get_pad_template_caps()
    logger.debug(
        'Source %s. Added pad %s on element %s. Caps: %s.',
        config.source_id,
        src_pad.get_name(),
        element.get_name(),
        caps,
    )
    codec = caps_to_codec(caps)
    if config.metadata_output:
        src_pad.add_probe(Gst.PadProbeType.BUFFER, log_frame_metadata, config)
    else:
        src_pad.add_probe(Gst.PadProbeType.BUFFER, delete_frame_from_pipeline, config)

    if codec.value.is_raw:
        capsfilter = factory.create(
            PipelineElement(
                'capsfilter',
                properties={'caps': caps},
            )
        )
        pipeline.add(capsfilter)
        assert capsfilter.get_static_pad('src').link(sink_pad) == Gst.PadLinkReturn.OK
        demuxer_peer_pad: Gst.Pad = capsfilter.get_static_pad('sink')
        capsfilter.sync_state_with_parent()
    else:
        decodebin = factory.create(PipelineElement('decodebin'))
        decodebin.set_property('sink-caps', caps)
        pipeline.add(decodebin)
        demuxer_peer_pad: Gst.Pad = decodebin.get_static_pad('sink')
        decodebin.connect('element-added', on_decodebin_element_added, config)
        decodebin.connect('pad-added', link_added_pad, sink_pad)
        decodebin.sync_state_with_parent()
        logger.debug(
            'Source %s. Added decoder %s.',
            config.source_id,
            decodebin.get_name(),
        )

    if config.sync:
        queue = factory.create(
            PipelineElement(
                'queue',
                properties={
                    'max-size-buffers': config.sync_queue_size,
                    'max-size-bytes': 0,
                    'max-size-time': 0,
                },
            )
        )
        pipeline.add(queue)
        assert (
            queue.get_static_pad('src').link(demuxer_peer_pad) == Gst.PadLinkReturn.OK
        )
        demuxer_peer_pad = queue.get_static_pad('sink')
        queue.sync_state_with_parent()

    assert src_pad.link(demuxer_peer_pad) == Gst.PadLinkReturn.OK


def build_input_pipeline(
    config: Config,
    last_frame: LastFrameRef,
    factory: GstElementFactory,
):
    pipeline: Gst.Pipeline = Gst.Pipeline.new('input-pipeline')
    zeromq_src_properties = {
        'source-id': config.source_id,
        'socket': config.zmq_endpoint,
        'max-width': config.max_allowed_resolution[0],
        'max-height': config.max_allowed_resolution[1],
        'pipeline': config.video_pipeline,
        'pipeline-stage-name': config.pipeline_source_stage_name,
    }
    savant_rs_video_demux_properties = {
        'pipeline': config.video_pipeline,
        'pipeline-stage-name': config.pipeline_demux_stage_name,
    }

    source_elements = [
        PipelineElement(
            'zeromq_src',
            properties=zeromq_src_properties,
        ),
        PipelineElement(
            'savant_rs_video_demux',
            properties=savant_rs_video_demux_properties,
        ),
    ]
    sink_elements = [
        PipelineElement(config.converter),
        PipelineElement(
            'capsfilter',
            properties={'caps': f'{config.video_raw_caps}, format=RGBA'},
        ),
    ]
    if config.sync:
        sink_elements.append(
            PipelineElement(
                'adjust_timestamps',
                properties={'adjust-first-frame': True},
            )
        )
    sink_elements.append(
        PipelineElement(
            'always_on_rtsp_frame_sink',
            properties={
                'last-frame': last_frame,
                'realtime': config.realtime,
                'sync-offset': (
                    config.sync_offset_ms * Gst.MSECOND if config.sync else -1
                ),
            },
        )
    )

    gst_source_elements = add_elements(pipeline, source_elements, factory)
    gst_sink_elements = add_elements(pipeline, sink_elements, factory)
    savant_rs_video_demux = gst_source_elements[-1]
    converter = gst_sink_elements[0]

    savant_rs_video_demux.connect(
        'pad-added',
        on_demuxer_pad_added,
        config,
        pipeline,
        factory,
        converter.get_static_pad('sink'),
    )

    return pipeline
