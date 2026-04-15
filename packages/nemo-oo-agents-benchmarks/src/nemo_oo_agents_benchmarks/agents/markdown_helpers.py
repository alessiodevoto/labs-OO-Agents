# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Markdown helper functions for document parsing.

These functions are available to LLM-generated code at runtime for
extracting and navigating markdown documentation.
"""

import re


def find_sections_matching_regex(
    markdown_content: str, pattern: str, flags: int = re.IGNORECASE
) -> dict[str, dict]:
    """Find sections by header regex -> dict[name, {level, line_count, char_count}].

    Searches section headers (titles) for matches against the given regex pattern.
    Returns detailed info including section size and header level.

    Args:
        markdown_content: The full markdown text to search
        pattern: Regex pattern to match against section headers
        flags: Regex flags (default: re.IGNORECASE for case-insensitive matching)

    Returns:
        Dict mapping section names to info:
        {
            "Fee Calculation": {"level": 2, "line_count": 25, "char_count": 1250},
            "Transaction Fees": {"level": 3, "line_count": 12, "char_count": 580}
        }

    Example:
        # Find all sections mentioning fees or charges
        fee_sections = find_sections_matching_regex(manual, r"fee|charge|cost")
        for section, info in fee_sections.items():
            print(f"{section} (level {info['level']}): {info['line_count']} lines")

        # Find sections about specific card types
        card_sections = find_sections_matching_regex(manual, r"visa|mastercard|amex")
    """
    try:
        compiled = re.compile(pattern, flags)
    except re.error:
        return {}

    lines = markdown_content.split("\n")
    sections: list[tuple[str, int, int]] = []  # (name, level, start_line)

    # Find all section headers with their positions
    for i, line in enumerate(lines):
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            header = line.lstrip("#").strip()
            if header:
                sections.append((header, level, i))

    matching: dict[str, dict] = {}
    for idx, (name, level, start) in enumerate(sections):
        # Only process headers that match the pattern
        if not compiled.search(name):
            continue

        # Find where this section ends
        end = len(lines)
        for _next_name, next_level, next_start in sections[idx + 1 :]:
            if next_level <= level:
                end = next_start
                break

        section_lines = lines[start:end]
        char_count = sum(len(line) + 1 for line in section_lines)

        matching[name] = {
            "level": level,
            "line_count": len(section_lines),
            "char_count": char_count,
        }

    return matching


def find_sections_with_content_matching_regex(
    markdown_content: str, pattern: str, flags: int = re.IGNORECASE
) -> dict[str, dict]:
    r"""Find sections by content regex -> dict[name, {match_count, line_count, previews}].

    Searches the body text of each section (not just headers) for matches.
    Returns detailed info about each matching section including match count and previews.

    Args:
        markdown_content: The full markdown text to search
        pattern: Regex pattern to match against section content
        flags: Regex flags (default: re.IGNORECASE)

    Returns:
        Dict mapping section names to match info:
        {
            "Section Name": {
                "match_count": 5,
                "line_count": 42,
                "previews": ["...context around match 1...", "...match 2...", "...match 3..."]
            }
        }

    Example:
        # Find sections that mention "fraud" anywhere in the content
        fraud_sections = find_sections_with_content_matching_regex(manual, r"fraud")
        for section, info in fraud_sections.items():
            print(f"{section}: {info['match_count']} matches")
            for preview in info['previews']:
                print(f"  - {preview}")

        # Find sections with specific numeric patterns
        rate_sections = find_sections_with_content_matching_regex(manual, r"\d+(\.\d+)?%")
    """
    try:
        compiled = re.compile(pattern, flags)
    except re.error:
        return {}

    lines = markdown_content.split("\n")
    sections: list[tuple[str, int, int]] = []  # (name, level, start_line)

    # Find all section headers with their positions
    for i, line in enumerate(lines):
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            header = line.lstrip("#").strip()
            if header:
                sections.append((header, level, i))

    matching: dict[str, dict] = {}
    for idx, (name, level, start) in enumerate(sections):
        # Find where this section ends
        end = len(lines)
        for _next_name, next_level, next_start in sections[idx + 1 :]:
            if next_level <= level:
                end = next_start
                break

        # Get section content and find all matches
        section_lines = lines[start:end]
        section_content = "\n".join(section_lines)
        all_matches = list(compiled.finditer(section_content))

        if all_matches:
            # Generate previews for first 3 matches with context
            previews = []
            for match in all_matches[:3]:
                # Get context around match (40 chars before and after)
                match_start = match.start()
                match_end = match.end()
                context_start = max(0, match_start - 40)
                context_end = min(len(section_content), match_end + 40)

                # Build preview string
                prefix = "..." if context_start > 0 else ""
                suffix = "..." if context_end < len(section_content) else ""
                context = section_content[context_start:context_end]
                # Clean up newlines for display
                context = context.replace("\n", " ").strip()
                previews.append(f"{prefix}{context}{suffix}")

            matching[name] = {
                "match_count": len(all_matches),
                "line_count": len(section_lines),
                "previews": previews,
            }

    return matching


def get_markdown_section(markdown_content: str, section_header: str) -> str:
    """Get a specific section from markdown content by header name.

    Args:
        markdown_content: The full markdown text to search
        section_header: The header text to find (without # prefix)

    Returns:
        The content of the section (from header to next same-level or higher header),
        or empty string if not found.

    Example:
        fee_info = get_markdown_section(manual, "Fee Calculation")
    """
    lines = markdown_content.split("\n")
    result_lines = []
    in_section = False
    section_level = 0

    # Normalize the search header (remove leading #s and whitespace)
    search_header = section_header.strip().lstrip("#").strip().lower()

    for line in lines:
        # Check if this is a header line
        if line.startswith("#"):
            # Count header level
            level = len(line) - len(line.lstrip("#"))
            header_text = line.lstrip("#").strip().lower()

            if in_section:
                # If we hit a same-level or higher header, stop
                if level <= section_level:
                    break
                # Otherwise include this subheader
                result_lines.append(line)
            elif header_text == search_header or search_header in header_text:
                # Found the section we're looking for
                in_section = True
                section_level = level
                result_lines.append(line)
        elif in_section:
            result_lines.append(line)

    return "\n".join(result_lines).strip()


def list_markdown_sections(markdown_content: str) -> list[str]:
    """List all section headers in a markdown document.

    Args:
        markdown_content: The full markdown text to scan

    Returns:
        List of section headers found (with # prefixes removed)

    Example:
        sections = list_markdown_sections(manual)
        print(sections)  # ['Introduction', 'Fee Calculation', 'Card Schemes', ...]
    """
    sections = []
    for line in markdown_content.split("\n"):
        if line.startswith("#"):
            header = line.lstrip("#").strip()
            if header:
                sections.append(header)
    return sections


def get_markdown_section_sizes(markdown_content: str) -> list[tuple[str, int]]:
    """Get section headers with their character counts.

    Args:
        markdown_content: The full markdown text to scan

    Returns:
        List of (section_name, char_count) tuples for each section.
        char_count includes the section content until the next same-level header.

    Example:
        sizes = get_markdown_section_sizes(manual)
        for name, size in sizes:
            print(f"{name}: {size:,} chars")
    """
    lines = markdown_content.split("\n")
    sections: list[tuple[str, int, int]] = []  # (name, level, start_line)

    # Find all section headers with their positions
    for i, line in enumerate(lines):
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            header = line.lstrip("#").strip()
            if header:
                sections.append((header, level, i))

    # Calculate sizes for each section
    result: list[tuple[str, int]] = []
    for idx, (name, level, start) in enumerate(sections):
        # Find where this section ends (next same-level or higher header)
        end = len(lines)
        for _next_name, next_level, next_start in sections[idx + 1 :]:
            if next_level <= level:
                end = next_start
                break

        # Calculate character count for this section
        section_lines = lines[start:end]
        char_count = sum(len(line) + 1 for line in section_lines)  # +1 for newline
        result.append((name, char_count))

    return result
