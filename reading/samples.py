from reading.framing import Dialect, InvalidReading, RecordFrame, sample_width


def decode_sample(sample: bytes, dialect: Dialect) -> int:
    if len(sample) != sample_width(dialect):
        raise ValueError('incorrect sample width')
    return sum(sample)


def sum_record_samples(frame: RecordFrame, dialect: Dialect) -> int:
    width = sample_width(dialect)
    if len(frame.sample_bytes) != frame.sample_count * width:
        raise InvalidReading(frame.start, 'incorrect sample payload length')
    return sum(decode_sample(frame.sample_bytes[start:start + width], dialect)
               for start in range(0, len(frame.sample_bytes), width))
