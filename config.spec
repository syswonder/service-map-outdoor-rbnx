specVersion: 1
description: Read-only GNSS and gait collection; no chassis commands.
properties:
  example_data:
    type: boolean
    default: false
    description: Mark synthetic fixture runs in snapshots and the Scene status.
  data_dir:
    type: string
    default: rbnx-build/data
  gnss_provider_id:
    type: string
  gait:
    type: object
    description: 'Actual feedback: contract/provider_id or explicit topic, msg_type
      and dotted field.'
  lifecycle_topic:
    type: string
    default: /robonix/outdoor_map/lifecycle
  limits:
    type: object
    description: input_timeout_s, join_radius_m, max_recording_bytes, max_points,
      max_recordings, max_storage_bytes.
