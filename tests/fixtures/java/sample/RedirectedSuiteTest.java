package sample;

import junit.framework.Test;
import junit.framework.TestCase;
import junit.framework.TestSuite;

public class RedirectedSuiteTest extends TestCase {
    public static Test suite() {
        return new TestSuite(OtherLegacyTest.class);
    }

    public void testOwn() {}
}
