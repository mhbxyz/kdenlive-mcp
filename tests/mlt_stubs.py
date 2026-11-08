"""Canned MLT producers written by the melt stubs (shared by conftest and tests)."""

AV_PRODUCER = """<?xml version="1.0"?><mlt><producer id="producer0" in="0" out="59">
 <property name="length">60</property><property name="eof">pause</property>
 <property name="resource">x</property><property name="audio_index">1</property>
 <property name="video_index">0</property><property name="seekable">1</property>
 <property name="meta.media.nb_streams">2</property><property name="mlt_service">avformat</property>
</producer></mlt>"""
AUDIO_PRODUCER = AV_PRODUCER.replace(
    '<property name="video_index">0</property>', '<property name="video_index">-1</property>'
).replace('<property name="audio_index">1</property>', '<property name="audio_index">0</property>')
