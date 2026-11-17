# Load the shared build configuration from the repository root.
my $audit_config_loaded = 0;
for my $audit_parent ('..', '../..') {
    next unless -f "$audit_parent/template/auditframework.sty";
    my $audit_config_result = do "$audit_parent/.latexmkrc";
    die "Cannot load audit build configuration: $@ $!" unless $audit_config_result;
    $audit_config_loaded = 1;
    last;
}
die "Cannot find the audit repository root" unless $audit_config_loaded;
