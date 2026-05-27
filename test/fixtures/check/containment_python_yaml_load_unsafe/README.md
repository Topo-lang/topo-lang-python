Calls `yaml.load(text)` without an explicit SafeLoader. Default loader allows arbitrary Python object construction (CVE-2017-18342). Expected: violation (issue #6).
