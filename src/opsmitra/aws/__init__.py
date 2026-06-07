"""AWS-specific implementations for OpsMitra.

Submodules are NOT eagerly imported to keep AWS isolated from the default
import path. Import them explicitly when needed:

    from opsmitra.aws.athena_source import AthenaEventSource
    from opsmitra.aws.s3_sink import S3NDJSONEventSink
    from opsmitra.aws.query_builder import build_event_query
"""
