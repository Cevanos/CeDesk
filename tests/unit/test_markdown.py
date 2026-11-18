"""An imported article's HTML as Markdown (md P6): the help center shows Markdown without
raw HTML, so before this a Zendesk or Intercom article showed its tags as text."""

from cedesk.helpdesk.domain.markdown import from_html, from_text, has_html


def test_a_help_desk_article_keeps_its_headings_lists_and_bold() -> None:
    """The live article from staging, cut short: it showed `<p class="no-margin">` text."""
    html = (
        '<p class="no-margin">If it arrives damaged, tell us within 30 days.</p>'
        '<h2 id="h_7">Your refund</h2><ul>'
        '<li><p class="no-margin"><b>Up to $100:</b> refunded straight away.</p></li>'
        '<li><p class="no-margin"><b>Over $100: </b>a team member approves.</p></li></ul>'
        '<p class="no-margin">We refund what you paid.</p>'
    )
    assert from_html(html) == (
        "If it arrives damaged, tell us within 30 days.\n\n"
        "## Your refund\n\n"
        "- **Up to $100:** refunded straight away.\n"
        "- **Over $100:** a team member approves.\n\n"
        "We refund what you paid."
    )  # the space inside </b> moves outside the stars, or they would not close


def test_nested_and_numbered_lists() -> None:
    html = (
        '<ol start="3"><li>First<ul><li>a</li><li><i>b</i></li></ul></li>'
        "<li>Second<p>more</p></li></ol><p>after</p>"
    )
    assert from_html(html) == ("3. First\n   - a\n   - *b*\n4. Second\n\n   more\n\nafter")


def test_an_li_left_open_still_makes_items() -> None:
    assert from_html("<ul><li>one<li>two</ul>") == "- one\n- two"
    assert from_html("<li>stray") == "- stray"


def test_text_reads_as_written() -> None:
    """Characters that would mark Markdown up are escaped, and so is a line that would
    start a heading, a quote or a list."""
    html = (
        "<p>order_id is *not* [bold] &lt;b&gt; &amp;copy;</p>"
        "<p>- dash</p><p># hash</p><p>2024. A year</p><p>&gt; not a quote</p>"
    )
    assert from_html(html) == (
        "order\\_id is \\*not\\* \\[bold\\] \\<b> \\&copy;\n\n"
        "\\- dash\n\n\\# hash\n\n2024\\. A year\n\n\\> not a quote"
    )


def test_only_web_and_mail_links_are_kept() -> None:
    html = (
        '<p><a href="https://ex.com/a_(b)">guide</a>, <a href="/hc/x">relative</a>, '
        '<a href="javascript:alert(1)">js</a>, <a href="mailto:a@b.co">mail</a>, '
        '<a href="https://ex.com"><img src="https://ex.com/i.png" alt="Logo"></a>, '
        '<a href="https://ex.com/empty"></a><img src="data:image/png;base64,AA" alt="pic">'
        '<img src="mailto:x@y.z"></p>'
    )
    assert from_html(html) == (
        "[guide](https://ex.com/a_%28b%29), relative, js, [mail](mailto:a@b.co), "
        "[![Logo](https://ex.com/i.png)](https://ex.com), pic"
    )


def test_line_breaks_code_and_preformatted_text() -> None:
    html = (
        "<p>One<br>Two<br/><br>Four</p>"
        "<pre><code>x = 1\n  y = `2`\n</code></pre>"
        "<p>Use <code>a_b</code> or <code>x`y</code></p>"
    )
    assert from_html(html) == (
        "One  \nTwo\n\nFour\n\n```\nx = 1\n  y = `2`\n```\n\nUse `a_b` or `` x`y ``"
    )


def test_quotes_tables_and_rules() -> None:
    html = (
        "<blockquote><p>Quoted</p><p>Two</p></blockquote>"
        "<table><tr><th>Plan</th><th>Price</th></tr>"
        "<tr><td><p>Basic</p></td><td><b>$5</b></td></tr></table><hr><p>end</p>"
    )
    assert from_html(html) == ("> Quoted\n>\n> Two\n\nPlan | Price  \nBasic | **$5**\n\n---\n\nend")


def test_scripts_styles_go_and_a_video_becomes_a_link() -> None:
    html = (
        "<div><script>evil()</script><style>p{}</style><p>kept <span>text</span></p>"
        '<iframe src="https://www.youtube.com/embed/xyz" title="How to"></iframe>'
        '<iframe src="javascript:x">fallback</iframe><p>after</p></div>'
    )
    assert from_html(html) == ("kept text\n\n[How to](https://www.youtube.com/embed/xyz)\n\nafter")


def test_marks_inside_marks_and_headings() -> None:
    html = "<p><b>outer <strong>inner</strong> done</b><b> </b>x</p><h3>Head<br>two</h3>"
    assert from_html(html) == "**outer inner done** x\n\n### Head two"
    assert from_html("<p><b>never closed</p><p>next</p>") == "**never closed**\n\nnext"


def test_empty_and_plain() -> None:
    assert from_html(None) == from_html("  ") == ""
    assert from_html("Just words, no tags") == "Just words, no tags"
    assert from_text("Hello_world\n\n- not a list\nline 2") == (
        "Hello\\_world\n\n\\- not a list  \nline 2"
    )
    assert from_text(None) == ""


def test_has_html_finds_tags_but_not_escaped_text() -> None:
    assert has_html("<p>x</p>") and has_html("a<br/>b") and has_html("<H2 id='a'>x</H2>")
    assert not has_html("plain text") and not has_html("a < b > c")
    assert not has_html(from_html("<p>a &lt;b&gt; c</p>"))  # converted: safe to run again
