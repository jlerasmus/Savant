# Auxiliary Streams

A pipeline demonstrating the use of Auxiliary Streams in Savant. The pipeline contains element, [Multiple Resolutions](multiple_resolutions.py). It scales the frame to multiple resolution and sends the frames to the auxiliary streams.

Tested on platforms:

- Nvidia Turing, Ampere
- Nvidia Jetson Orin family

## Prerequisites

```bash
git clone https://github.com/insight-platform/Savant.git
cd Savant
git lfs pull
./utils/check-environment-compatible
```

**Note**: Ubuntu 22.04 runtime configuration [guide](https://insight-platform.github.io/Savant/develop/getting_started/0_configure_prod_env.html) helps to configure the runtime to run Savant pipelines.

## Run Demo

```bash
# you are expected to be in Savant/ directory

# if x86
docker compose -f samples/auxiliary_streams/docker-compose.x86.yml up

# if Jetson
docker compose -f samples/auxiliary_streams/docker-compose.l4t.yml up

# open 'rtsp://127.0.0.1:554/stream/video-360p', 'rtsp://127.0.0.1:554/stream/video-480p',
# 'rtsp://127.0.0.1:554/stream/video-720p' in your player
# or visit 'http://127.0.0.1:888/stream/video-360p', 'http://127.0.0.1:888/stream/video-480p', 
# 'http://127.0.0.1:888/stream/video-720p' (LL-HLS)

# All the streams on AO-sink are 1280x720 for simplicity of deployment.
# You can see the different quality of the streams since AO-sink upscales them to 1280x720.

# Ctrl+C to stop running the compose bundle
```

**Note**: If you meet an issue with encoder (e.g. `RuntimeError: Received error "gst-core-error-quark: ENCODER INITIALIZATION FAILED (1)" from nvv4l2h264enc2.`), you can try to decrease auxiliary streams, comment one of the sections in the `demo.yml`, e.g.:
```yaml
        resolutions:
          # suffix for source ID of the auxiliary stream
          - suffix: '-360p'
            width: 640
            height: 360
          #- suffix: '-480p'
          #  width: 856
          #  height: 480
          - suffix: '-720p'
            width: 1280
            height: 720
```