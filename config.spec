specVersion: 1
description: Read-only GNSS and gait collection; no chassis commands.
properties:
  locomotion_mode:
    type: string
    enum: [legged, wheeled]
    default: legged
    description: Wheeled mode records GNSS without a gait subscription or invented gait codes; routes retain quality intervals and omit gait commands.
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
      and dotted field. Ignored entirely in wheeled mode. Missing code_map preserves the legacy legged mapping.'
    properties:
      contract: {type: string}
      provider_id: {type: string}
      topic: {type: string}
      msg_type: {type: string}
      field: {type: string}
      code_map:
        type: object
        description: 'Raw uint32 codes (decimal or 0x hex keys) to flat/stairs/unknown. Replaces the entire default mapping; unmapped codes remain unknown. Persisted with recordings and maps.'
        additionalProperties:
          type: string
          enum: [flat, stairs, unknown]
  lifecycle_topic:
    type: string
    default: /robonix/outdoor_map/lifecycle
  limits:
    type: object
    description: input_timeout_s, join_radius_m, max_recording_bytes, max_points,
      max_recordings, max_storage_bytes.
