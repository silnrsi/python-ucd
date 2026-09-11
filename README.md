# ucd Module

## Summary

A wrapper module around the Unicode Character Database which dynamically caches the latest data, compresses and gives access to nearly all the UCD data

This module contains most of the ucd information for every character in Unicode.

## Usage

```python
    from ucd import get_ucd, get_info, find_ucd, get_enums
    print(get_ucd(0x0041, 'scx'))
    print(get_info(0x0041))
    print(get_enums("indic position"))
    print(find_ucd("indic position", "Bottom")
```

Note cjk properties are not supported for space reasons.

If you want to use your own data file (perhaps the module data is stale) the use
the object interface:

```python
    from ucd import UCD
    myucd = UCD(localfile="ucd.nounihan.flat.zip")   # localfile falls back to bundled data
    print(myucd.get(0x0041, 'scx'))
```

The second parameter specifies the property to be queried and can either be a property from
https://www.unicode.org/reports/tr42, especially section 4.4 Properties, or the start of a
full property name from that list.

For characters not yet in Unicode, data for additional characters can
be temporarily appended to the bundled data:

```python
    from ucd import get_ucd, loadxml
    loadxml("extra-ucd.xml")
```

or, with the object interface:

```python
    from ucd import UCD
    myucd = UCD().loadxml("extra-ucd.xml")
```

The named file must be coded in the same form as the "flat" UCD XML data, though the only
required character attributes are "cp" and anything needed by the calling process. For example:

```xml
    <?xml version="1.0" encoding="utf-8" standalone="yes"?>
    <ucd xmlns="http://www.unicode.org/ns/2003/ucd/1.0">
        <description>Some additional characters</description>
        <repertoire>
            <char cp="10EC2" age="16.0" gc="Lo" bc="AL" na="ARABIC LETTER DAL WITH TWO DOTS VERTICALLY BELOW"></char>
            <char cp="10EC3" age="16.0" gc="Lo" bc="AL" na="ARABIC LETTER TAH WITH TWO DOTS VERTICALLY BELOW"></char>
            <char cp="10EC4" age="16.0" gc="Lo" bc="AL" na="ARABIC LETTER KAF WITH TWO DOTS VERTICALLY BELOW"></char>
        </repertoire>
    </ucd>
```

