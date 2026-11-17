# Shared configuration, used from the root or through a report's .latexmkrc.
use Cwd qw(abs_path);
use File::Basename qw(dirname);

my $audit_repo_root = abs_path('.');
while (!-f "$audit_repo_root/template/auditframework.sty") {
    my $audit_parent = dirname($audit_repo_root);
    die "Cannot find template/auditframework.sty" if $audit_parent eq $audit_repo_root;
    $audit_repo_root = $audit_parent;
}

# Keep report-specific files first; include only the active shared components.
# The trailing colon retains TeX's standard package search paths.
$ENV{'TEXINPUTS'} = join(':', '.', "$audit_repo_root/template",
    "$audit_repo_root/template/sections", "$audit_repo_root/template/assets", '');
$pdflatex = 'pdflatex -synctex=1 %O %S';

1;
